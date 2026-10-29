"""Synthetic fixtures only; never represented as live job opportunities."""
import json
import plistlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jobhunter.model import Job, canonical_url, plain, url_identity
from jobhunter.report import display_salary, write_reports
from jobhunter.scheduler import plist_data
from jobhunter.screen import geography, role_match, screen
from jobhunter.service import load_config, next_due, perform_run, run_lock
from jobhunter.sources import SourceError, fetch_source, parse_jobs
from jobhunter.state import Store

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc)
PROFILE = json.loads((ROOT / 'profile.example.json').read_text())
SOURCE = {'provider': 'ashby', 'board': 'fixture', 'company': 'Synthetic Fixture'}


def job(identifier='a', **changes):
    fields = dict(provider='ashby', board='fixture', id=identifier, company='Synthetic Fixture',
                  title='Senior QA Automation Engineer', location='Remote — Indonesia', workplace='remote',
                  description='Build automation frameworks with Python and Playwright. API testing with Postman. Integrate CI/CD pipelines and mentor engineers.',
                  url='https://jobs.ashbyhq.com/fixture/' + identifier,
                  apply_url='https://jobs.ashbyhq.com/fixture/' + identifier + '/application')
    fields.update(changes)
    return Job(**fields)


class SourceTests(unittest.TestCase):
    def test_greenhouse_updated_is_not_publication(self):
        source = {'provider': 'greenhouse', 'board': 'fixture', 'company': 'Fixture'}
        data = {'jobs': [{'id': 1, 'title': 'SDET', 'updated_at': NOW.isoformat(), 'location': {'name': 'Worldwide'},
                          'absolute_url': 'https://boards.greenhouse.io/fixture/jobs/1', 'content': '&lt;p&gt;Python&lt;/p&gt;'}]}
        found = parse_jobs(source, data)[0]
        self.assertIsNone(found.published_at)
        self.assertEqual(found.description, 'Python')

    def test_ashby_isremote_does_not_override_hybrid(self):
        data = {'jobs': [{'id': 'x', 'title': 'SDET', 'location': 'Indonesia', 'isRemote': True, 'workplaceType': 'Hybrid',
                          'jobUrl': 'https://jobs.ashbyhq.com/fixture/x', 'descriptionPlain': 'Python automation', 'isListed': True}]}
        self.assertEqual(geography(parse_jobs(SOURCE, data)[0])[0], 'Ineligible')

    def test_unlisted_ashby_job_not_exposed(self):
        self.assertEqual(parse_jobs(SOURCE, {'jobs': [{'id': 'private', 'isListed': False}]}), [])

    def test_malformed_and_incomplete_responses_are_failures(self):
        for data in ({}, {'jobs': None}, {'jobs': [{'title': 'missing id'}]}):
            with self.assertRaises(SourceError):
                parse_jobs(SOURCE, data)
        with self.assertRaises(SourceError):
            parse_jobs({'provider': 'greenhouse', 'board': 'x', 'company': 'x'}, {'jobs': [], 'meta': {'total': 10}})

    def test_lever_pagination_and_created_date(self):
        calls = []
        def getter(url, timeout):
            calls.append(url)
            if 'skip=0' not in url:
                return []
            return [{'id': str(i), 'text': 'SDET', 'hostedUrl': 'https://jobs.lever.co/fixture/' + str(i),
                     'applyUrl': '', 'createdAt': 1780000000000, 'categories': {'location': 'APAC'}} for i in range(100)]
        found = fetch_source({'provider': 'lever', 'board': 'fixture', 'company': 'Fixture'}, getter=getter)
        self.assertEqual(len(found), 100)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(found[0].published_at)
        self.assertIn('skip=100', calls[1])


class ScreeningTests(unittest.TestCase):
    def test_indonesia_worldwide_and_apac_are_distinct(self):
        for location, expected in [('Indonesia', 'Verified'), ('Global Anywhere', 'Verified'), ('Asia / Taipei', 'Likely'), ('Remote', 'Unclear'), ('IND-Remote', 'Ineligible'), ('Remote - United States', 'Ineligible')]:
            with self.subTest(location=location):
                self.assertEqual(geography(job(location=location))[0], expected)

    def test_specific_location_overrides_global_marketing(self):
        j = job(location='EMEA', description='Our global team works worldwide. Python automation. Fully remote.')
        self.assertEqual(geography(j)[0], 'Ineligible')
        self.assertNotEqual(geography(job(location='Anywhere / EMEA'))[0], 'Verified')

    def test_body_restrictions_and_timezone(self):
        for text in ['Remote within India', 'Work remotely (Germany-wide)', 'Must be based in Canada', 'Located CET ±3']:
            with self.subTest(text=text):
                self.assertEqual(geography(job(location='Worldwide', description=text))[0], 'Ineligible')

    def test_exclusion_of_other_country_is_not_indonesia_blocker(self):
        self.assertEqual(geography(job(location='Worldwide', description='You must not be based in India. Fully remote.'))[0], 'Verified')

    def test_unknown_salary_keeps_five_points_and_review(self):
        a = screen(job(), PROFILE, NOW)
        self.assertEqual(a['components']['C'], .5)
        self.assertEqual(a['recommendation'], 'Review')
        self.assertEqual(a['freshness'], 'Not available')

    def test_publication_and_deadline_do_not_fake_open_freshness(self):
        a = screen(job(published_at='2026-10-28T00:00:00Z', deadline='2026-10-25T00:00:00Z'), PROFILE, NOW)
        self.assertEqual(a['freshness'], '7 hari')
        self.assertIn('Unconfirmed', a['status'])
        self.assertEqual(a['recommendation'], 'Review')

    def test_keyword_gaps_are_not_all_mandatory(self):
        a = screen(job(description='Python or Go. Playwright or Cypress. Build automation frameworks.'), PROFILE, NOW)
        self.assertIn('cypress', a['mentions_unproven'])
        self.assertEqual(a['recommendation'], 'Review')

    def test_role_filter_does_not_match_manufacturing(self):
        self.assertFalse(role_match(job(title='Quality Assurance Manager', description='Food safety and manufacturing inspections')))
        self.assertTrue(role_match(job(title='Software Engineer in Test')))

    def test_junior_scope_not_promoted(self):
        self.assertEqual(screen(job(title='Junior QA Automation Engineer'), PROFILE, NOW)['recommendation'], 'Skip')

    def test_office_and_specific_city_not_promoted_to_remote(self):
        self.assertEqual(geography(job(location='Office Based - Beijing, China', workplace='unknown'))[0], 'Ineligible')
        self.assertEqual(geography(job(location='Metro Manila'))[0], 'Ineligible')

    def test_foreign_authorization_question_downgrades_global(self):
        j = job(location='Global', metadata={'application_questions': ['Are you legally authorized to work in the United States?']})
        self.assertEqual(screen(j, PROFILE, NOW)['geography'], 'Unclear')

    def test_required_core_not_diluted_by_optional_known_skills(self):
        j = job(description='Who You Are (Must-Haves)\nGo programming experience. Kubernetes and Helm.\nWho You Might Be (Bonus Points)\nPython, TypeScript, Playwright, Selenium, Java, Postman and SQL.')
        self.assertLess(screen(j, PROFILE, NOW)['technical'], 70)


class StateAndSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'jobs.sqlite3')
        self.run = self.store.start_run(NOW.isoformat())

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def ingest(self, jobs):
        return self.store.ingest('ashby:fixture', [(j, screen(j, PROFILE, NOW)) for j in jobs], self.run, NOW.isoformat())

    def test_new_unchanged_scope_change_and_reappearance(self):
        self.assertEqual(self.ingest([job()])[0]['kind'], 'new')
        self.assertEqual(self.ingest([job(updated_at='2026-10-30')]), [])
        self.assertEqual(self.ingest([job(title='Staff QA Automation Engineer')])[0]['kind'], 'updated')
        missing = self.ingest([])[0]
        self.assertEqual(missing['kind'], 'not_listed')
        self.assertNotIn('Closed', missing['assessment']['status'])
        self.assertEqual(self.ingest([job()])[0]['kind'], 'reappeared')

    def test_manual_history_suppresses_first_snapshot(self):
        url = 'https://jobs.ashbyhq.com/fixture/12345678-1234-1234-1234-123456789abc/application?utm_source=x'
        path = self.root / 'history.md'
        path.write_text(url)
        self.assertEqual(self.store.seed_history(path, NOW.isoformat()), 1)
        self.assertEqual(self.ingest([job(identifier='12345678-1234-1234-1234-123456789abc')]), [])
        self.assertEqual(self.store.seed_history(path, NOW.isoformat()), 0)

    def test_month_boundary_every_three_days_at_nine(self):
        cfg = {'timezone': 'Asia/Jakarta', 'interval_days': 3, 'hour': 9, 'minute': 0}
        last = datetime(2026, 1, 30, 8, 0, tzinfo=timezone.utc)
        due = next_due(cfg, last, last)
        self.assertEqual(due.isoformat(), '2026-02-02T09:00:00+07:00')

    def test_concurrent_run_lock(self):
        with run_lock(self.root):
            with self.assertRaises(RuntimeError):
                with run_lock(self.root):
                    pass

    def test_url_normalization_preserves_semantic_params(self):
        result = canonical_url('https://example.org/jobs?gh_jid=22&utm_source=x&country=ID#apply')
        self.assertEqual(result, 'https://example.org/jobs?gh_jid=22&country=ID')
        self.assertEqual(url_identity('https://job-boards.greenhouse.io/alpaca/jobs/6114845004'), 'greenhouse:alpaca:6114845004')


class IntegrationTests(unittest.TestCase):
    def test_greenhouse_salary_cents_are_displayed_as_currency_units(self):
        value = json.dumps([{'min_cents': 10000000, 'max_cents': 12000000, 'currency_type': 'USD', 'title': 'US only', 'blurb': '<p>ignore raw html</p>'}])
        self.assertEqual(display_salary(value), 'USD 100,000–120,000 · US only (periode/gross-net belum dikonfirmasi)')

    def test_report_failure_does_not_lose_new_job_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'profile.json').write_text(json.dumps(PROFILE))
            cfg = dict(_root=str(root), _path=str(root / 'config.json'), data_dir=str(root / 'data'), report_dir=str(root / 'reports'),
                       profile=str(root / 'profile.json'), history_file=str(root / 'history.md'), timezone='Asia/Jakarta',
                       interval_days=3, hour=9, minute=0, freshness_days=7, limit=10, sources=[SOURCE])
            with patch('jobhunter.service.write_reports', side_effect=OSError('simulated disk failure')):
                with self.assertRaises(OSError):
                    perform_run(cfg, fetcher=lambda s, t: [job()], now=NOW)
            recovered = perform_run(cfg, fetcher=lambda s, t: [job()], now=NOW)
            self.assertEqual(recovered['events'][0]['kind'], 'new')
            unchanged = perform_run(cfg, fetcher=lambda s, t: [job()], now=NOW)
            self.assertEqual(unchanged['events'], [])

    def test_failure_preserves_previous_jobs_and_reports_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'profile.json').write_text(json.dumps(PROFILE))
            cfg = dict(_root=str(root), _path=str(root / 'config.json'), data_dir=str(root / 'data'), report_dir=str(root / 'reports'),
                       profile=str(root / 'profile.json'), history_file=str(root / 'history.md'), timezone='Asia/Jakarta',
                       interval_days=3, hour=9, minute=0, freshness_days=7, limit=10, sources=[SOURCE])
            first = perform_run(cfg, fetcher=lambda s, t: [job()], now=NOW)
            self.assertEqual(first['summary']['new'], 1)
            def fail(source, timeout):
                raise SourceError('simulated network failure')
            failed = perform_run(cfg, fetcher=fail, now=NOW)
            self.assertEqual(failed['outcome'], 'failed')
            self.assertEqual(failed['events'], [])
            self.assertFalse(failed['active'][0]['checked_this_run'])
            self.assertIn('belum diperiksa ulang', (root / 'reports/latest.html').read_text())
            cfg['sources'].append({'provider': 'ashby', 'board': 'other', 'company': 'Other'})
            def partial(source, timeout):
                if source['board'] == 'other':
                    raise SourceError('simulated failure')
                return [job()]
            self.assertEqual(perform_run(cfg, fetcher=partial, now=NOW)['outcome'], 'partial')

    def test_html_escapes_untrusted_posting_and_no_script_url(self):
        with tempfile.TemporaryDirectory() as temp:
            j = job(title='<script>alert(1)</script> SDET', apply_url='javascript:alert(1)')
            row = {'kind': 'new', 'job': j.dict(), 'assessment': screen(j, PROFILE, NOW), 'checked_this_run': True}
            result = dict(events=[row], active=[row], sources=[], run_id=1, checked_local='fixture', outcome='success', summary={'successful_sources': 0, 'screened': 1})
            write_reports(Path(temp), result, 10)
            content = (Path(temp) / 'latest.html').read_text()
            self.assertNotIn('<script>alert', content)
            self.assertNotIn('href="javascript:', content)

    def test_launch_agent_uses_argument_array_for_paths_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='QA Job Hunter ') as temp:
            data = plist_data({'_root': temp, '_path': temp + '/config.json', 'hour': 9, 'minute': 0})
            restored = plistlib.loads(plistlib.dumps(data))
            self.assertEqual(restored['ProgramArguments'][-2:], [temp + '/config.json', 'tick'])
            self.assertEqual(restored['StartInterval'], 900)


if __name__ == '__main__':
    unittest.main()
