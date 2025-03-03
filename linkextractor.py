import asyncio
import re
from httpx import AsyncClient, Timeout
from urllib.parse import urlparse, urljoin
from collections import OrderedDict
import subprocess
from bs4 import BeautifulSoup
import validators
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

status_cache = {}

class LimitedSet:
    """A set-like structure with a maximum size limit, evicting oldest items."""
    def __init__(self, max_size=10000):
        self._data = OrderedDict()
        self._max_size = max_size

    def add(self, item):
        if item in self._data:
            self._data.move_to_end(item)
        elif len(self._data) >= self._max_size:
            self._data.popitem(last=False)
        self._data[item] = None

    def __contains__(self, item):
        return item in self._data

async def get_status_code_with_cache(session, url, expected_status):
    """Check URL status with caching, no retries."""
    if url in status_cache:
        return status_cache[url]
    try:
        response = await session.get(url)
        status_cache[url] = response.status_code
        if response.status_code == expected_status:
            return url
        return None
    except Exception as e:
        logging.warning(f"Failed to fetch {url}: {str(e)}")
        return None

async def extract_links(session, url):
    """Extract links from various HTML elements and inline JavaScript."""
    try:
        response = await session.get(url)
        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        links = []

        # Extract from <a> tags
        for tag in soup.find_all("a", href=True):
            link = tag["href"]
            if link.startswith("/"):
                link = urljoin(url, link)
            if validators.url(link):
                links.append(link)

        # Extract from <img>, <script>, <link>, <iframe>
        for tag in soup.find_all(['img', 'script', 'link', 'iframe'], src=True):
            link = tag['src']
            if link.startswith("/"):
                link = urljoin(url, link)
            if validators.url(link):
                links.append(link)

        # Extract from inline JavaScript
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string:
                js_links = re.findall(r'(?:https?://|/)[\w/.-]+(?:\?\S+)?', script.string)
                for link in js_links:
                    if link.startswith("/"):
                        link = urljoin(url, link)
                    if validators.url(link):
                        links.append(link)

        return links
    except Exception as e:
        logging.warning(f"Error extracting links from {url}: {str(e)}")
        return []

async def run_tool(command, domain):
    """Run an external tool asynchronously."""
    try:
        proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().splitlines()
        else:
            logging.warning(f"Error running {command[0]} for {domain}: {stderr.decode()}")
            return []
    except Exception as e:
        logging.warning(f"Error running {command[0]} for {domain}: {str(e)}")
        return []

async def run_gau(domain):
    return await run_tool(["gau", domain], domain)

async def run_waybackurls(domain):
    return await run_tool(["waybackurls", domain], domain)

async def crawl_links(session, seed_url, domain, include_subdomains, depth, expected_status, rate_limit, visited):
    """Recursively crawl links with domain filtering before status checks."""
    if depth < 1 or seed_url in visited:
        return []
    visited.add(seed_url)
    semaphore = asyncio.Semaphore(rate_limit)
    async with semaphore:
        links = await extract_links(session, seed_url)
        valid_links = []
        # Filter by domain first
        for link in links:
            parsed_link = urlparse(link)
            if (include_subdomains and parsed_link.netloc.endswith(domain)) or \
               (not include_subdomains and parsed_link.netloc == domain):
                valid_links.append(link)

        # Check status only for valid links
        tasks = [get_status_code_with_cache(session, link, expected_status) for link in valid_links]
        valid_links_with_status = await asyncio.gather(*tasks)
        valid_links_with_status = [link for link in valid_links_with_status if link]

        if depth > 1:
            next_tasks = [crawl_links(session, link, domain, include_subdomains, depth - 1, expected_status, rate_limit, visited)
                          for link in valid_links_with_status]
            next_links = await asyncio.gather(*next_tasks)
            valid_links_with_status.extend([item for sublist in next_links for item in sublist])

        return valid_links_with_status

async def crawl_website(seed_urls, domain, include_subdomains, expected_status, depth, rate_limit, use_gau, use_wayback, output_file, timeout=20):
    """Crawl websites with configurable timeout and parallel external tools."""
    async with AsyncClient(
        verify=False,
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'},
        timeout=Timeout(timeout)
    ) as session:
        all_links = set()
        visited = LimitedSet(max_size=10000)  # Limit to 10,000 URLs

        try:
            # Run external tools concurrently
            tool_tasks = []
            if use_gau:
                tool_tasks.append(run_gau(domain))
            if use_wayback:
                tool_tasks.append(run_waybackurls(domain))
            
            if tool_tasks:
                tool_results = await asyncio.gather(*tool_tasks)
                for result in tool_results:
                    all_links.update(result)

            tasks = [crawl_links(session, seed_url, domain, include_subdomains, depth, expected_status, rate_limit, visited)
                     for seed_url in seed_urls]
            results = await asyncio.gather(*tasks)
            flat_results = [item for sublist in results for item in sublist]
            all_links.update(flat_results)

            if output_file:
                with open(output_file, "w") as f:
                    for url in all_links:
                        f.write(str(url) + "\n")
        except Exception as e:
            logging.error(f"Error in crawl_website: {str(e)}")
            return []

        return list(all_links)