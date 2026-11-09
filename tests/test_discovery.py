"""Synthetic directory fixtures; no network requests."""
import io
import json
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from jobhunter.discovery import board_from_url, check_url, discover_sources, extract_boards, parse_directory


def archive(profiles):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as tar:
        for slug, text in profiles.items():
            data = text.encode()
            info = tarfile.TarInfo('repo/src/companies/' + slug + '.md')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def profile(slug, region='worldwide', url=None):
    return f'---\ntitle: "Synthetic {slug}"\nslug: {slug}\nregion: {region}\ncareers_url: {url or "https://example.org/" + slug}\n---\n## How to apply\n'


class DiscoveryTests(unittest.TestCase):
    def test_exact_hosts_and_embedded_board(self):
        self.assertEqual(board_from_url('https://boards.greenhouse.io/embed/job_board?for=fixture'), ('greenhouse', 'fixture'))
        self.assertEqual(board_from_url('https://job-boards.greenhouse.io/fixture/jobs/123'), ('greenhouse', 'fixture'))
        self.assertIsNone(board_from_url('https://jobs.lever.co.attacker.org/fake'))
        self.assertIsNone(board_from_url('http://jobs.lever.co/fixture'))
        self.assertIsNone(board_from_url('https://[malformed'))
        self.assertEqual(extract_boards('https:\\/\\/jobs.ashbyhq.com\\/fixture\\/123'), [('ashby', 'fixture')])

    def test_directory_is_not_job_data_and_path_traversal_not_read(self):
        companies, _ = parse_directory(archive({'a': profile('a', url='https://jobs.lever.co/fixture')}))
        self.assertEqual(companies[0]['boards'], [['lever', 'fixture']])
        self.assertNotIn('geography', companies[0])
        with self.assertRaises(ValueError):
            parse_directory(archive({}))

    def test_private_networks_refused(self):
        with patch('jobhunter.discovery.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                check_url('https://example.org/careers')
        with self.assertRaises(ValueError):
            check_url('https://user:secret@example.org/careers')

    def test_rotation_validation_cache_failure_and_region_not_promoted(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = dict(data_dir=temp+'/data', report_dir=temp+'/reports', sources=[{'provider':'ashby','board':'existing','company':'Original'}],
                       discovery={'enabled':True,'max_career_pages':1,'max_new_boards':5})
            payload = archive({'a':profile('a'), 'b':profile('b', 'asia-pacific'), 'us':profile('us','americas')})
            now = datetime(2026,11,9,tzinfo=timezone.utc)
            calls = []
            def reader(url, *args):
                calls.append(url)
                return (payload, url) if 'codeload' in url else (b'<a href="https://jobs.lever.co/fixture">jobs</a>', url)
            def validator(source, timeout):
                return []
            sources, summary = discover_sources(cfg, now, reader, lambda *a:{'sha':'a'*40}, validator)
            self.assertEqual(summary['directory_companies'], 3)
            self.assertEqual(summary['region_candidates'], 2)
            self.assertEqual(len(sources), 2)
            self.assertNotIn('geography', sources[-1])
            sources2, summary2 = discover_sources(cfg, now+timedelta(days=1), reader, lambda *a:self.fail('must use cache'), validator)
            self.assertEqual(len(sources2), 2)
            self.assertIn('https://example.org/b', calls)
            self.assertNotIn('https://example.org/us', calls)
            def fail(*args):
                raise OSError('synthetic unavailable')
            sources3, summary3 = discover_sources(cfg, now+timedelta(days=8), reader, fail, validator)
            self.assertEqual(len(sources3), 2)
            self.assertTrue(summary3['warnings'])
            self.assertTrue(Path(temp+'/reports/discovery.md').is_file())

    def test_bad_board_not_promoted(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg=dict(data_dir=temp+'/data',report_dir=temp+'/reports',sources=[],discovery={'enabled':True})
            payload=archive({'a':profile('a', url='https://jobs.lever.co/fixture')})
            def fail(*args):
                raise ValueError('synthetic dead ATS')
            sources, summary=discover_sources(cfg, datetime.now(timezone.utc), lambda url,*a:(payload,url),lambda *a:{'sha':'a'*40},fail)
            self.assertEqual(sources, [])
            self.assertEqual(summary['new_boards'], 0)
            saved=json.loads(Path(temp+'/data/discovery.json').read_text())
            self.assertIn('lever:fixture', saved['validation_failures'])

    def test_disabled_board_is_not_reenabled(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg=dict(data_dir=temp+'/data',report_dir=temp+'/reports',sources=[],discovery={'enabled':True},_disabled_sources=['lever:fixture'])
            payload=archive({'a':profile('a',url='https://jobs.lever.co/fixture')})
            sources,_=discover_sources(cfg,datetime.now(timezone.utc),lambda url,*a:(payload,url),lambda *a:{'sha':'a'*40},lambda *a:self.fail('disabled source must not be probed'))
            self.assertEqual(sources, [])

    def test_discovered_sources_reach_search_and_warnings_are_partial(self):
        from jobhunter.service import perform_run
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            example=Path(__file__).resolve().parent.parent/'profile.example.json'
            cfg=dict(data_dir=temp+'/data',report_dir=temp+'/reports',profile=str(example),history_file=temp+'/absent.md',
                     sources=[{'provider':'ashby','board':'original','company':'Original'}], timezone='Asia/Jakarta',freshness_days=7,limit=10)
            discovered={'provider':'lever','board':'fixture','company':'Synthetic'}
            seen=[]
            def fetch(source, timeout):
                seen.append(source['board'])
                return []
            with patch('jobhunter.discovery.discover_sources',return_value=(cfg['sources']+[discovered],{'enabled':True,'warnings':['synthetic upstream failure; cached boards retained']})):
                result=perform_run(cfg,fetcher=fetch)
            self.assertEqual(set(seen),{'original','fixture'})
            self.assertEqual(result['outcome'],'partial')
            self.assertEqual(result['summary']['successful_sources'],2)
            self.assertIn('synthetic upstream failure',(root/'reports/latest.md').read_text())
