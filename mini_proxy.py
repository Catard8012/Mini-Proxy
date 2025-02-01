from flask import Flask, request, Response, render_template_string
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin, urlparse

app = Flask(__name__)

# HTML Template for the home page
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mini Proxy</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            text-align: center;
            margin-top: 50px;
        }
        input[type="text"] {
            width: 70%;
            padding: 10px;
            font-size: 1.2em;
        }
        button {
            padding: 10px 20px;
            font-size: 1.2em;
            cursor: pointer;
        }
        .error {
            color: red;
            margin-top: 20px;
        }
    </style>
    <script>
        // Overload fetch to route through proxy
        const originalFetch = window.fetch;
        window.fetch = function(url, options = {}) {
            if (url.startsWith('/proxy?url=')) {
                // If the URL starts with /proxy, let it go through the proxy system
                return originalFetch(url, options);
            } else {
                // Otherwise, rewrite the URL to go through the proxy
                return originalFetch('/proxy?url=' + encodeURIComponent(url), options);
            }
        };

        // Overload XMLHttpRequest to route through proxy
        const originalXHR = XMLHttpRequest;
        XMLHttpRequest = function() {
            const xhr = new originalXHR();
            const originalOpen = xhr.open;
            xhr.open = function(method, url, async, user, password) {
                if (!url.startsWith('/proxy?url=')) {
                    url = '/proxy?url=' + encodeURIComponent(url);
                }
                return originalOpen.call(this, method, url, async, user, password);
            };
            return xhr;
        };
    </script>
</head>
<body>
    <h1>Mini Proxy</h1>
    <form action="/proxy" method="get">
        <input type="text" name="url" placeholder="Enter a URL to browse" required>
        <button type="submit">Browse</button>
    </form>
    {% if error %}
        <div class="error">{{ error }}</div>
    {% endif %}
</body>
</html>
"""

# Add a Content Security Policy header to all responses
@app.after_request
def add_csp_header(response):
    # Define the Content Security Policy
    csp = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self';"
    response.headers['Content-Security-Policy'] = csp
    return response


@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/proxy')
def proxy():
    # Get the URL from the query string
    url = request.args.get('url')
    if not url:
        return render_template_string(HTML_TEMPLATE, error="Please enter a valid URL.")

    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url

    try:
        # Standard browser headers to avoid being blocked by Fortinet and other services
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'max-age=0',
        }

        # Perform the request
        response = requests.get(url, headers=headers, allow_redirects=True)

        # Check if the response is HTML
        if 'text/html' in response.headers.get('Content-Type', ''):
            # Parse the HTML using BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')

            # Inject the script to overload fetch and XMLHttpRequest
            for script_tag in soup.find_all('script'):
                # Skip inline scripts and only modify external JS
                if script_tag.get('src'):
                    src = script_tag['src']
                    script_tag['src'] = rewrite_url(url, src)

            # Modify links to resources (CSS, JS, images, etc.)
            for tag in soup.find_all(['link', 'script', 'img', 'a']):
                if tag.name == 'link' and tag.get('rel') == ['stylesheet']:
                    href = tag.get('href')
                    tag['href'] = rewrite_url(url, href)
                elif tag.name == 'script' and tag.get('src'):
                    src = tag.get('src')
                    tag['src'] = rewrite_url(url, src)
                elif tag.name == 'img' and tag.get('src'):
                    src = tag.get('src')
                    tag['src'] = rewrite_url(url, src)
                elif tag.name == 'a' and tag.get('href'):
                    href = tag.get('href')
                    tag['href'] = rewrite_url(url, href)

            for tag in soup.find_all('link', {'rel': 'stylesheet'}):
                css_response = requests.get(url, headers=headers)
                css_content = css_response.text
                modified_css = rewrite_css_urls(url, css_content)

            # Handle inline styles (e.g., background images)
            for tag in soup.find_all(style=True):
                modified_style = rewrite_css_urls(url, tag['style'])
                tag['style'] = modified_style

            # Return the modified HTML content
            return Response(str(soup), status=response.status_code, content_type='text/html')
        else:
            # If the content is not HTML, return it as-is
            return Response(response.content, status=response.status_code, content_type=response.headers.get('Content-Type'))

    except requests.exceptions.RequestException as e:
        return render_template_string(HTML_TEMPLATE, error=f"Error fetching URL: {str(e)}")


def rewrite_url(base_url, resource_url):
    """
    Rewrites resource URLs to be proxied by the Flask app.
    """
    # If the resource URL is absolute, we just return it as it is
    if resource_url.startswith(('http://', 'https://')):
        # Avoid double proxying
        if resource_url.startswith(base_url):
            return '/proxy?url=' + resource_url
        return resource_url

    # If it's a relative URL, join it with the base URL
    return '/proxy?url=' + urljoin(base_url, resource_url)

def rewrite_css_urls(base_url, css_content):
    """
    Rewrites URLs inside CSS files (e.g., url('...')) to go through the proxy.
    """
    # Regex to find all `url(...)` patterns
    url_pattern = re.compile(r'url\(["\']?(.*?)["\']?\)', re.IGNORECASE)
    
    def replace_url(match):
        resource_url = match.group(1)
        if resource_url.startswith(('http://', 'https://')):
            if resource_url.startswith(base_url):
                return '/proxy?url=' + resource_url
            else:
                return f'url({resource_url})'  # Leave absolute URLs unchanged
        return f'url({rewrite_url(base_url, resource_url)})'  # Rewrite relative URLs

    return re.sub(url_pattern, replace_url, css_content)


if __name__ == '__main__':
    app.run(debug=True)