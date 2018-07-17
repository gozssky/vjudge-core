import re
from abc import abstractmethod
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from bs4.element import NavigableString

from .. import exceptions
from ..base import BaseClient

BASE_URL = 'http://acm.hdu.edu.cn'

LANG_ID = {'G++': '0', 'GCC': '1', 'C++': '2',
           'C': '3', 'Pascal': '4', 'Java': '5', 'C#': '6'}

PAGE_TITLES = {'Problem Description': 'description', 'Input': 'input', 'Output': 'output',
               'Sample Input': 'sample_input', 'Sample Output': 'sample_output'}


class _UniClient(BaseClient):
    def __init__(self, auth=None, client_type='practice', contest_id=0, timeout=5):
        super().__init__()
        self.auth = auth
        self.client_type = client_type
        self.contest_id = contest_id
        self.timeout = timeout
        if auth is not None:
            self.username, self.password = auth
            self.login(self.username, self.password)

    @abstractmethod
    def get_name(self):
        pass

    def get_user_id(self):
        if self.auth is None:
            raise exceptions.LoginRequired
        return self.username

    def login(self, username, password):
        url = self._get_login_url()
        data = {
            'login': 'Sign in',
            'username': username,
            'userpass': password
        }
        try:
            r = self._session.post(url, data, timeout=self.timeout)
        except requests.exceptions.RequestException:
            raise exceptions.ConnectionError
        if re.search('Sign In Your Account', r.text):
            raise exceptions.LoginError
        self.auth = (username, password)
        self.username = username
        self.password = password

    @abstractmethod
    def check_login(self):
        pass

    def update_cookies(self):
        if self.auth is None:
            raise exceptions.LoginRequired
        self.login(self.username, self.password)

    def get_problem(self, problem_id):
        url = self._get_problem_url(problem_id)
        try:
            r = self._session.get(url, timeout=self.timeout)
        except requests.exceptions.RequestException:
            raise exceptions.ConnectionError
        return self._parse_problem(r.text)

    def submit_problem(self, problem_id, language, source_code):
        if self.auth is None:
            raise exceptions.LoginRequired
        if language not in LANG_ID:
            raise exceptions.SubmitError(f'Language "{language}" is not supported')
        if self.client_type == 'contest':
            source_code = self.__class__._encode_source_code(source_code)
        data = {
            'problemid': problem_id,
            'language': LANG_ID[language],
            'usercode': source_code
        }
        if self.client_type == 'contest':
            data['submit'] = 'Submit'
        else:
            data['check'] = '0'
        url = self._get_submit_url()
        try:
            r = self._session.post(url, data, timeout=self.timeout)
            r.encoding = 'GBK'
            if re.search('Sign In Your Account', r.text):
                raise exceptions.LoginExpired
            if re.search('Code length is improper', r.text):
                raise exceptions.SubmitError('Code length is too short')
            if not re.search('Realtime Status', r.text):
                raise exceptions.SubmitError
            status_url = self._get_status_url(problem_id=problem_id, user_id=self.username)
            r = self._session.get(status_url, timeout=self.timeout)
        except requests.exceptions.RequestException:
            raise exceptions.ConnectionError
        try:
            table = BeautifulSoup(r.text, 'lxml').find('div', id='fixed_table').table
            run_id = next(table.find('tr', align="center").stripped_strings)
        except (AttributeError, StopIteration):
            raise exceptions.SubmitError
        return run_id

    def get_submit_status(self, run_id, **kwargs):
        if self.auth is None and self.client_type == 'contest':
            raise exceptions.LoginRequired
        user_id = kwargs.get('user_id', '')
        problem_id = kwargs.get('problem_id', '')
        url = self._get_status_url(run_id=run_id, problem_id=problem_id, user_id=user_id)
        try:
            r = self._session.get(url, timeout=self.timeout)
            r.encoding = 'GBK'
        except requests.exceptions.RequestException:
            raise exceptions.ConnectionError
        if re.search('Sign In Your Account', r.text):
            raise exceptions.LoginExpired
        result = self.__class__._find_verdict(r.text, run_id)
        if result is not None:
            return result
        if self.client_type == 'contest':
            for page in range(2, 5):
                status_url = url + f'&page={page}'
                try:
                    r = self._session.get(status_url, timeout=self.timeout)
                    r.encoding = 'GBK'
                except requests.exceptions.RequestException:
                    raise exceptions.ConnectionError
                result = self.__class__._find_verdict(r.text, run_id)
                if result is not None:
                    return result

    def _get_login_url(self):
        login_url = f'{BASE_URL}/userloginex.php?action=login'
        if self.client_type == 'contest':
            login_url += f'&cid={self.contest_id}&notice=0'
        return login_url

    def _get_submit_url(self):
        if self.client_type == 'contest':
            return f'{BASE_URL}/contests/contest_submit.php?action=submit&cid={self.contest_id}'
        else:
            return f'{BASE_URL}/submit.php?action=submit'

    def _get_status_url(self, run_id='', problem_id='', user_id=''):
        if self.client_type == 'contest':
            return (f'{BASE_URL}/contests/contest_status.php?'
                    f'cid={self.contest_id}&pid={problem_id}&user={user_id}&lang=0&status=0')
        else:
            return f'{BASE_URL}/status.php?first={run_id}&pid={problem_id}&user={user_id}&lang=0&status=0'

    def _get_problem_url(self, problem_id):
        if self.client_type == 'contest':
            return f'{BASE_URL}/contests/contest_showproblem.php?pid={problem_id}&cid={self.contest_id}'
        else:
            return f'{BASE_URL}/showproblem.php?pid={problem_id}'

    def _parse_problem(self, text):
        result = {}
        pattern = re.compile((r'Time Limit:.*?[0-9]*/([0-9]*).*?MS.*?\(Java/Others\).*?'
                              'Memory Limit:.*?[0-9]*/([0-9]*).*?K.*?\(Java/Others\)'))
        # find time limit and mem limit
        limit = re.search(pattern, text)
        if limit:
            result['time_limit'] = limit.group(1)
            result['mem_limit'] = limit.group(2)
        soup = BeautifulSoup(text, 'lxml')
        # replace relative url
        img_tags = soup.find_all('img')
        for tag in img_tags:
            img_url = urljoin(self._get_problem_url(''), tag['src'])
            tag['src'] = img_url
        if soup.h1:
            result['title'] = soup.h1.text
            if result['title'] == 'System Message':
                return
        tags = soup.find_all('div', 'panel_title', align='left')
        for t in tags:
            title = t.string
            if title in PAGE_TITLES:
                tag = t.next_sibling
                limit = 0
                while tag and type(tag) is NavigableString and limit < 3:
                    tag = tag.next_sibling
                    limit += 1
                if tag is None or type(tag) is NavigableString:
                    continue
                res = re.match('<div.*?>(.*)</div>$', str(tag), re.DOTALL)
                if res:
                    result[PAGE_TITLES[title]] = res.group(1)
        return result

    @staticmethod
    def _find_verdict(text, run_id):
        soup = BeautifulSoup(text, 'lxml')
        tables = soup.find_all('table')
        table = None
        for t in tables:
            if re.search(r'Run ID.*Judge Status.*Author', str(t), re.DOTALL):
                table = t
        if table is None:
            return
        tags = table.find_all('tr', align="center")
        for tag in tags:
            result = [x.text for x in tag.find_all('td')]
            if len(result) < 6:
                continue
            if result[0] == run_id:
                verdict = result[2]
                try:
                    exe_time = int(result[4].replace('MS', ''))
                    exe_mem = int(result[5].replace('K', ''))
                except ValueError:
                    continue
                if re.search('Runtime Error', verdict):
                    verdict = 'Runtime Error'
                return verdict, exe_time, exe_mem

    @staticmethod
    def _encode_source_code(code):
        from urllib import parse
        import base64
        return base64.b64encode(parse.quote(code).encode('utf-8')).decode('utf-8')


class HDUClient(_UniClient):
    def __init__(self, auth=None, **kwargs):
        super().__init__(auth, **kwargs)
        self.name = 'hdu'

    def get_name(self):
        return self.name

    def check_login(self):
        url = BASE_URL + '/control_panel.php'
        try:
            r = self._session.get(url, timeout=self.timeout)
        except requests.exceptions.RequestException:
            raise exceptions.ConnectionError
        if re.search('Sign In Your Account', r.text):
            return False
        return True


class HDUContestClient(_UniClient):
    def __init__(self, auth=None, contest_id=None, **kwargs):
        timeout = kwargs.get('timeout', 5)
        if contest_id is None:
            raise exceptions.JudgeException('You must specific a contest id')
        super().__init__(auth, 'contest', contest_id, timeout)
        self.name = f'hdu_ct_{contest_id}'

    def get_name(self):
        return self.name

    def check_login(self):
        return True