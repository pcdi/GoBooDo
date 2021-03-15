import argparse
import asyncio
import json
import os
import random
from io import BytesIO

import aiohttp
import pytesseract
import requests
from PIL import Image

parser = argparse.ArgumentParser(description='Welcome to GoBooDoAsync')
parser.add_argument("--id")
args = parser.parse_args()

# load config
with open('settings.json') as ofile:
    settings = json.load(ofile)


class GoBooDoAsync:
    def __init__(self, book_id):
        self.path = os.path.join(os.getcwd(), book_id)
        self.data_path = os.path.join(self.path, 'data')
        self.image_path = os.path.join(self.path, 'images')
        self.found = False
        if os.path.isdir(self.data_path):
            self.found = True
        else:
            os.mkdir(self.path)
            os.mkdir(self.data_path)
        if not os.path.isdir(self.image_path):
            os.mkdir(self.image_path)
        self.id = book_id
        self.name = self.id
        self.country = settings['country']
        self.proxy_list_path = settings['proxy_list_path']
        self.timeout = aiohttp.ClientTimeout(total=settings['global_retry_time'])
        self.reset_head()
        self.page_resolution = settings['page_resolution']
        self.tesseract_path = settings['tesseract_path']
        self.page_link_coro_list = []
        self.current_page = ""
        self.page_data = {}
        self.params = {}
        if os.path.isfile(os.path.join(self.data_path, 'page_list.json')):
            with open(os.path.join(self.data_path, 'page_list.json'), 'r') as ofile:
                self.page_list = json.load(ofile)
        else:
            self.page_list = []
        self.pageLinkDict = {}
        self.lastCheckedPage = ""
        self.obstinatePages = []
        self.use_proxy = bool(settings['proxy_links'])
        if self.use_proxy is True:
            self.http_prefix = "http://"
        else:
            self.http_prefix = "https://"
        if self.proxy_list_path:
            try:
                req = requests.get(self.proxy_list_path, verify=False)
                req.raise_for_status()
                proxies = req.text
                with open('proxies.txt', 'w+') as ofile:
                    ofile.write(proxies)
                self.plist = proxies.splitlines()
            except Exception as e:
                if req.status_code == 404:
                    print("The proxy list could not be found. \n")
                else:
                    print(f'Error while getting proxies: {e}')
        else:
            with open('proxies.txt', 'r') as ofile:
                self.plist = ofile.readlines()

    def reset_head(self):
        try:
            req = requests.get("https://google." + self.country, verify=False)
            self.head = {
                'Host': 'books.google.' + self.country,
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64; rv:53.0) Gecko/20100101 Firefox/53.00',
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'close',
                'Cookie': "NID=" + str(req.cookies['NID']),
            }
        except Exception as e:
            if 'captcha'.encode() in req.content:
                print(
                    "IP detected by Google for too much requests, asking for captcha completion. Please wait some "
                    "minutes before trying again. \n")
            else:
                print(f'Error while resetting head: {e}')

    def get_proxy(self):
        proxy = random.choice(self.plist)
        return proxy.strip()

    async def get_page_link_coro_list(self) -> list:
        return self.page_link_coro_list

    async def append_to_page_link_coro_list(self, page):
        self.page_link_coro_list.append(self.get_page_link(page))

    async def get_page_list(self):
        if self.page_list:
            return
        link_url: str = self.http_prefix + "books.google." + self.country + "/books"
        self.params = {'id': str(self.id), 'printsec': 'frontcover', 'jscmd': 'click3'}
        proxy = None

        try:
            if self.use_proxy is True:
                proxy = 'http://' + self.get_proxy()
                print(f'Using proxy {proxy} for page list.')
            else:
                print(f'Fetching page list.')
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                try:
                    async with session.get(link_url,
                                           params=self.params,
                                           headers=self.head,
                                           proxy=proxy) as resp:
                        assert resp.status == 200
                        self.page_list = await resp.json()
                        self.page_list = self.page_list['page']
                        if self.page_list is None:
                            raise TypeError(f'Page list was None.')
                        print(f'Got page list.')
                        with open(os.path.join(self.data_path, 'page_list.json'), 'w+') as ofile:
                            json.dump(self.page_list, ofile)
                except AssertionError as e:
                    print(f'AssertionError for page list, retrying. ({e})')
                    await self.get_page_list()
                except asyncio.TimeoutError as e:
                    print(f'Timeout for page list, retrying.')
                    await self.get_page_list()
        except Exception as e:
            print(f'Could not connect for page list, retrying. ({e})')
            await self.get_page_list()

    async def create_page_list(self):
        for page in self.page_list:
            if 'src' in page:
                print(f'Already fetched page link for page {page["pid"]}, continuing.')
                continue
            else:
                await self.append_to_page_link_coro_list(page['pid'])

    async def get_page_link(self, page):
        link_url: str = self.http_prefix + "books.google." + self.country + "/books"
        self.params = {'id': str(self.id), 'pg': str(page), 'jscmd': 'click3'}
        proxy = None
        try:
            if self.use_proxy is True:
                proxy = 'http://' + self.get_proxy()
                print(f'Using proxy {proxy} for the URL of page {page}.')
            else:
                print(f'Fetching URL of page {page}.')
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                try:
                    async with session.get(link_url,
                                           params=self.params,
                                           headers=self.head,
                                           proxy=proxy) as resp:
                        assert resp.status == 200
                        page_data = await resp.json()
                        assert page_data is not None
                        page_data = page_data['page']
                        current_page_data = page_data[0]
                        if 'src' not in current_page_data and 'order' not in current_page_data:
                            raise KeyError(f'"src" or "order" for {page} was None.')
                        else:
                            self.page_list = [
                                current_page_data if dict_entry['pid'] == current_page_data['pid'] else dict_entry
                                for dict_entry in self.page_list]
                            # for page in page_data:
                            #     if 'src' in page:
                            #         self.page_list = [
                            #             page if dict_entry['pid'] == page['pid'] else dict_entry
                            #             for dict_entry in self.page_list]
                            print(f'Got URL of page {page}.')
                            return page_data
                except AssertionError as e:
                    print(f'AssertionError page {page}, retrying. ({e})')
                    return await self.get_page_link(page)
                except asyncio.TimeoutError as e:
                    print(f'Timeout for page {page}, retrying.')
                    return await self.get_page_link(page)
        except Exception as e:
            print(f'Could not connect for the URL of page {page}, retrying. ({e})')
            return await self.get_page_link(page)

    async def get_image_from_page_data(self):
        link = self.page_data['src'].replace('https://', self.http_prefix, 1)
        page_order_number = self.page_data['order']
        page_logical_number = self.page_data['pid']
        if os.path.isfile(os.path.join(self.image_path, str(page_order_number) + ".png")):
            print(f'Page {page_logical_number} already exists, continuing.')
            return
        else:
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    proxy = None
                    if self.use_proxy is True:
                        proxy = 'http://' + self.get_proxy()
                        print(f'Using proxy {proxy} for image of page {page_logical_number}.')
                    else:
                        print(f'Fetching image of page {page_logical_number}.')
                    async with session.get(link + '&w=' + str(self.page_resolution), headers=self.head,
                                           proxy=proxy) as resp:
                        assert resp.status == 200
                        page_image_binary = await resp.read()
                    print(f'Fetched image for page {page_logical_number}')
                    im = Image.open(BytesIO(page_image_binary))
                    if self.is_page_empty(im) is True:
                        raise TypeError(f'Page {page_logical_number} not available, retrying.')
                    im.save(os.path.join(self.image_path, str(page_order_number) + ".png"))
                    print(f'Saved page {page_logical_number}.')
            except AssertionError as e:
                print(f'Page {page_logical_number} not available, retrying.')
                await self.get_image_from_page_data()
            except asyncio.TimeoutError as e:
                print(f'Timeout for image {page_logical_number}, retrying.')
                await self.get_image_from_page_data()
            except Exception as e:
                print(f'Could not get image, retrying. ({e})')
                await self.get_image_from_page_data()

    def is_page_empty(self, image):
        im = image
        width, height = im.size
        im = im.resize((int(width / 5), int(height / 5)))
        gray = im.convert('L')
        text = ""
        try:
            text = pytesseract.image_to_string(gray)
        except:
            pytesseract.pytesseract.tesseract_cmd = '/usr/local/bin/tesseract'
            text = pytesseract.image_to_string(gray)
        finally:
            return text.strip().replace('\n', " ") == 'image not available'

    def make_pdf(self):
        print('Making PDF ...')
        if not os.path.exists(os.path.join(self.path, 'Output')):
            os.mkdir(os.path.join(self.path, 'Output'))
        sorted_file_list = sorted(os.listdir(self.image_path), key=lambda x: int(x[:-4]))
        image_path_list = [os.path.join(self.image_path, x) for x in sorted_file_list]
        first_path = image_path_list[0]
        name = self.id + '.pdf'
        pdf_path = os.path.join(self.path, 'Output', name)
        image_list = [Image.open(image_path) for image_path in image_path_list]
        first_image = Image.open(first_path)
        first_image.save(pdf_path, format='PDF', resolution=72, save_all=True, append_images=image_list[1:])
        print('Done.')


async def main():
    # make a list of all page links to be fetched
    # make a list of all images to fetch

    # add all pages to tasks "fetch link"

    # get link success:
    # remove from list, get image
    # get link fail:
    # keep on list, try again

    # get image success:
    # remove from list, save image
    # get image fail: keep on list, try again

    # fetch page links
    # create list of coros to get page links

    book_id = args.id
    if (book_id == None or len(book_id) != 12):
        print('No book id given or incorrect book id given')
        exit(0)
    book = GoBooDoAsync(book_id)
    try:
        await book.get_page_list()
        await book.create_page_list()
        page_link_coro_list = await book.get_page_link_coro_list()

        # Add to the task list: Images that were not previously downloaded but whose URL has already been fetched.
        for page_data in book.page_list:
            if 'src' in page_data and 'order' in page_data:
                book.page_data = page_data
                await book.get_image_from_page_data()
            else:
                continue

        # Fetch URLs
        for page_link_coro in asyncio.as_completed(page_link_coro_list):
            try:
                page_links = await page_link_coro
                book.page_data = page_links[0]
                await book.get_image_from_page_data()
            except Exception as e:
                print(f'Exception: {e}.')
    except Exception as e:
        print(f'{e}')
    finally:
        with open(os.path.join(book.data_path, 'page_list.json'), 'w+') as ofile:
            json.dump(book.page_list, ofile)
        book.make_pdf()


if __name__ == "__main__":
    print('GoBooDoAsync\n')
    asyncio.run(main())
