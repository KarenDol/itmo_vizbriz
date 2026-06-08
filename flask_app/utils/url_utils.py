"""
URL utilities for the VizBriz application.
Handles URL shortening using various services.
"""
import requests
import urllib.parse
import os
import logging

logger = logging.getLogger(__name__)


def shorten_url_with_tinyurl(long_url):
    """
    Shorten a long URL using the TinyURL API.
    Args:
        long_url (str): The original long URL.
    Returns:
        str: The shortened URL, or None if an error occurs.
    """
    try:
        # Use the new TinyURL API endpoint
        api_url = "https://api.tinyurl.com/create"
        
        # Get API token from environment variable
        api_token = os.environ.get('TINYURL_API_TOKEN')
        if not api_token:
            logger.warning("TINYURL_API_TOKEN not found in environment variables. Using fallback method.")
            # Fallback to the old API (will show interstitial page)
            api_url = f"http://tinyurl.com/api-create.php?url={urllib.parse.quote(long_url)}"
            response = requests.get(api_url, timeout=10)
            if response.status_code == 200:
                return response.text.strip()
            else:
                logger.error(f"TinyURL API returned status code {response.status_code}")
                return None
        
        # Use new authenticated API
        headers = {
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'url': long_url,
            'domain': 'tinyurl.com'  # Use default domain
        }
        
        response = requests.post(api_url, headers=headers, json=payload, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'tiny_url' in data['data']:
                return data['data']['tiny_url']
            else:
                logger.error(f"Unexpected response format from TinyURL API: {data}")
                return None
        else:
            logger.error(f"TinyURL API returned status code {response.status_code}: {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error shortening URL: {e}")
        return None


def shorten_url_with_rebrandly(long_url, api_key=None):
    """
    Shorten a long URL using the Rebrandly API (alternative to TinyURL).
    Args:
        long_url (str): The original long URL.
        api_key (str): Rebrandly API key. If None, will try to get from environment.
    Returns:
        str: The shortened URL, or None if an error occurs.
    """
    try:
        if not api_key:
            api_key = os.environ.get('REBRANDLY_API_KEY')
        
        if not api_key:
            logger.warning("REBRANDLY_API_KEY not found in environment variables.")
            return None
        
        api_url = "https://api.rebrandly.com/v1/links"
        
        headers = {
            'Content-Type': 'application/json',
            'apikey': api_key
        }
        
        payload = {
            'destination': long_url,
            'domain': {'fullName': 'rebrand.ly'}  # Use default domain
        }
        
        response = requests.post(api_url, headers=headers, json=payload, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'shortUrl' in data:
                return f"https://{data['shortUrl']}"
            else:
                logger.error(f"Unexpected response format from Rebrandly API: {data}")
                return None
        else:
            logger.error(f"Rebrandly API returned status code {response.status_code}: {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error shortening URL with Rebrandly: {e}")
        return None


def shorten_url(long_url, service='tinyurl'):
    """
    Shorten a URL using the specified service.
    Args:
        long_url (str): The original long URL.
        service (str): The service to use ('tinyurl' or 'rebrandly').
    Returns:
        str: The shortened URL, or the original URL if shortening fails.
    """
    if service == 'tinyurl':
        short_url = shorten_url_with_tinyurl(long_url)
    elif service == 'rebrandly':
        short_url = shorten_url_with_rebrandly(long_url)
    else:
        logger.error(f"Unknown URL shortening service: {service}")
        return long_url
    
    return short_url if short_url else long_url
