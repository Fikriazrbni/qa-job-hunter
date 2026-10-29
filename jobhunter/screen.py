"""Conservative keyword screening, never a substitute for semantic CV review."""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from .model import Job, parse_date

ROLE = re.compile(r"\bsdet\b|\bqa\b|quality (?:assurance|engineer|engineering)|(?:software|development) engineer (?:in|in a) test|test (?:automation|infrastructure|engineer)|automation test", re.I)
AUTOMATION = re.compile(r"automat|playwright|selenium|test framework|software testing|test infrastructure", re.I)
COUNTRIES = r"united states|united kingdom|canada|germany|poland|portugal|romania|india|philippines|australia|singapore|vietnam|taiwan|hong kong|brazil|argentina|mexico|japan|south africa|france|netherlands|spain|ukraine|serbia|croatia|czech|malaysia|thailand|new zealand|estonia|israel|ireland|sweden|finland|pakistan|bangladesh|sri lanka"
REGIONS = r"emea|europe|european union|latam|latin america|north america|united states|u\.?s\.?a?\.?|canada|uk|eu"
SKILLS = ["python", "java", "typescript", "javascript", "groovy", "playwright", "selenium", "robot framework", "katalon", "cucumber", "testng", "postman", "jenkins", "testops", "jira", "git", "sql", "fastapi", "n8n", "cypress", "appium", "pytest", "rest assured", "graphql", "docker", "kubernetes", "helm", "aws", "azure", "gcp", "bash", "linux", "go", "k6", "jmeter", "locust", "pact", "newman", "github actions", "gitlab"]
CORE = {"python", "java", "typescript", "javascript", "groovy", "playwright", "selenium", "robot framework", "katalon", "cypress", "appium", "pytest", "testng", "cucumber", "rest assured", "go"}


def contains(term: str, text: str) -> bool:
    if term == "go":
        return bool(re.search(r"\bgolang\b|\bGo\b(?=\s*(?:[,/+.):;]|programming|language|experience))", text))
    return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I))


def role_match(job: Job) -> bool:
    return bool(ROLE.search(job.title) and AUTOMATION.search(job.title + "\n" + job.description))


def geography(job: Job) -> tuple[str, str, list[str]]:
    loc = job.location.lower()
    desc = job.description.lower()
    workplace = job.workplace.lower()
    evidence = []
    if workplace in {"hybrid", "onsite", "on-site"}:
        return "Ineligible", workplace, [f"workplaceType={job.workplace}; bukan full remote"]
    if re.search(r'office.based|on.site|\bwfo\b', loc) and not re.search(r'home.based|remote|\bapac\b', loc):
        return 'Ineligible', 'onsite', ['Lokasi role menyebut office based/onsite']
    onsite = re.search(r"(?:full\s+wfo|fully\s+on.?site|hybrid work(?:ing)? arrangement|at least \d+ days? (?:per week )?in (?:the )?office)", desc)
    if onsite:
        return "Ineligible", "hybrid/onsite", [onsite.group(0)]
    if re.search(r"(?:exclud(?:e|es|ing)|except|not (?:open|available) to)[^\n.;]{0,100}\bindonesia\b", desc):
        return "Ineligible", workplace, ["Indonesia disebut dalam pengecualian"]
    if re.search(r"cet\s*(?:±|\+/-|\+\s*/\s*-)\s*3", desc):
        return "Ineligible", workplace, ["CET ±3 tidak mencakup UTC+7"]
    if re.search(r"remote[^\n.;]{0,25}germany.wide|remote within (?:the )?(?:" + COUNTRIES + r")", desc) and "indonesia" not in loc:
        return "Ineligible", "remote restricted", ["Isi membatasi remote ke negara lain"]
    remote = workplace == "remote" or bool(re.search(r"remote|home.based|anywhere|worldwide|global", loc))
    role_lines = "\n".join(line for line in desc.splitlines() if re.search(r"location|based in|remote|work from|hiring in|eligible countr|open to candidates", line))
    if not remote:
        remote = bool(re.search(r"fully remote|100% remote|remote (?:role|position|work)|work from (?:home|anywhere)", role_lines))
    if not remote:
        if re.search(r'\b(?:' + COUNTRIES + r'|beijing|metro manila|bengaluru|bangalore)\b', loc) and not re.search(r'\bapac\b|\basia\b|indonesia|worldwide|global|anywhere', loc):
            return 'Ineligible', 'unknown', ['Lokasi role di luar Indonesia dan tanpa cakupan remote Indonesia']
        return "Unclear", "unknown", ["Full remote belum dinyatakan jelas"]
    explicit_indonesia = bool(re.search(r"\bindonesia\b", loc) or re.search(r"(?:based in|candidates (?:from|in)|location|hiring in|open to)[^\n.;]{0,90}\bindonesia\b", role_lines))
    # Restriction clauses override generic global-team language and broad role labels.
    restriction = re.search(r"(?:must|need to|have to|only)[^\n.;]{0,45}(?:reside|resident|based|located)[^\n.;]{0,40}\b(" + COUNTRIES + r"|" + REGIONS + r")\b|\b(" + COUNTRIES + r"|" + REGIONS + r")[- ]only\b|\b(?:remote|remotely)\s*\(?(?:within|in|from)\s+(?:the\s+)?(" + REGIONS + r")\b", role_lines)
    if restriction and not explicit_indonesia and not re.search(r'\b(?:must|need to|have to)\s+not\b', restriction.group(0)):
        return "Ineligible", "remote restricted", [restriction.group(0)]
    if explicit_indonesia:
        return "Verified", "remote", ["Indonesia disebut pada lokasi/cakupan role"]
    asia = bool(re.search(r"\bapac\b|asia.pacific|\basia\b|southeast asia", loc))
    restricted_location = bool(re.search(r"\b(?:" + COUNTRIES + r"|" + REGIONS + r"|metro manila|beijing|bengaluru|bangalore)\b|\bind.remote\b", loc))
    worldwide = bool(re.search(r"worldwide|anywhere|\bglobal\b", loc))
    if restricted_location and not asia:
        if worldwide:
            return "Unclear", "remote", ["Label global bertentangan dengan negara/region lokasi: " + job.location]
        return "Ineligible", "remote restricted", ["Lokasi role terbatas: " + job.location]
    if asia:
        return "Likely", "remote", ["Cakupan Asia/APAC; konfirmasi entity Indonesia"]
    if worldwide:
        return "Verified", "remote", ["Cakupan global/anywhere pada lokasi role; konfirmasi kontrak tetap perlu"]
    if re.search(r"remote[^\n.;]{0,25}(?:worldwide|anywhere in the world)|(?:worldwide|globally)[^\n.;]{0,15}remote|work from anywhere in the world", role_lines):
        return "Verified", "remote", ["Role menyebut remote worldwide"]
    return "Unclear", "remote", ["Remote tidak menjelaskan negara kandidat"]


def screen(job: Job, profile: dict, now: datetime, freshness_days=7) -> dict:
    text = job.title + "\n" + job.description
    geo, arrangement, evidence = geography(job)
    questions = '\n'.join(job.metadata.get('application_questions', []))
    if geo in {'Verified', 'Likely'} and re.search(r'authori[sz]|sponsor', questions, re.I) and re.search(r'united states|\bUS\b|\bUSA\b|united kingdom|canada', questions, re.I):
        geo = 'Unclear'
        evidence.append('Pertanyaan authorization/sponsorship negara lain; cakupan Indonesia perlu dikonfirmasi')
    known = {s.lower() for s in profile["skills"]}
    mentioned = [skill for skill in SKILLS if contains(skill, text)]
    matched = [s for s in mentioned if s in known]
    gaps = [s for s in mentioned if s not in known]
    # Mentions are not automatically mandatory; alternative/bonus language needs human review.
    required_lines, section = [], False
    for line in job.description.splitlines():
        header = line.strip(' #*:\t').lower()
        if re.match(r'^(who you are|requirements|required qualifications|what we expect|must.haves|what you bring)\b', header):
            section = True
        elif re.match(r'^(who you might be|bonus|nice to have|preferred|benefits|how we take care|what we offer)\b', header):
            section = False
        elif section:
            required_lines.append(line)
    required_text = '\n'.join(required_lines)
    scoring_skills = [s for s in mentioned if contains(s, required_text)] if required_text else mentioned
    weighted_total = sum(2 if s in CORE else 1 for s in scoring_skills)
    weighted_match = sum(2 if s in CORE else 1 for s in scoring_skills if s in known)
    tech = .5 if not weighted_total else .35 + .65 * weighted_match / weighted_total
    tech = min(1, max(0, round(tech * 20) / 20))
    framework = bool(re.search(r"(?:build|design|develop|architect|maintain)[^\n.]{0,80}(?:framework|automation|tooling|infrastructure)", text, re.I))
    ci = bool(re.search(r"ci/cd|continuous integration|pipeline", text, re.I))
    mentor = bool(re.search(r"mentor|technical leadership|quality strategy", text, re.I))
    duties = .5 + .2 * framework + .1 * ci + .1 * mentor
    if re.search(r"\bjunior\b|entry.level|internship", job.title, re.I):
        duties = .35
    growth = .5 + .15 * framework + .1 * ci + .15 * mentor
    location_rating = {"Verified": 1 if re.search(r"\bindonesia\b", job.location, re.I) else .95, "Likely": .9, "Unclear": .5, "Ineligible": 0}[geo]
    components = {"T": tech, "R": round(duties, 2), "G": round(growth, 2), "L": location_rating, "C": .5}
    total = sum(Decimal(str(components[k])) * w for k, w in zip("TRGLC", [35, 25, 15, 15, 10]))
    score = int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    flags = ["Rules-only: semua kandidat tetap perlu review JD dan negara sebelum apply"]
    if job.metadata.get('detail_error'):
        flags.append('Detail/form API gagal dibaca; sebagian persyaratan mungkin belum terambil')
    if questions:
        flags.append('Pertanyaan aplikasi terkait eligibility: ' + questions.replace('\n', ' / '))
    years = re.findall(r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:professional\s+)?experience", job.description, re.I)
    experience = (now.date() - datetime.fromisoformat(profile["qa_started"]).date()).days / 365.25
    if any(int(y) > experience for y in years):
        flags.append("Ada angka pengalaman di atas masa QA profil; periksa required vs preferred")
    if re.search(r"citizen|citizenship|work authori[sz]|right to work|security clearance", text, re.I):
        flags.append("Citizenship/authorization perlu konfirmasi; belum diketahui dari CV")
    if re.search(r"fluent english|english.{0,12}(?:c1|c2|b2|upper.intermediate)", text, re.I):
        flags.append("English mungkin di atas intermediate yang dinyatakan")
    if re.search(r"timezone|time zone|overlap|working hours|australian|\b[PECAM][DS]T\b", text, re.I):
        flags.append("Periksa jam kerja/overlap; kesiapan kandidat belum diketahui")
    published = parse_date(job.published_at)
    age = (now - published).total_seconds() / 86400 if published else None
    freshness = "Not available" if age is None else "future/invalid" if age < 0 else "7 hari" if age <= freshness_days else "lebih lama"
    deadline = parse_date(job.deadline)
    closed = bool(deadline and deadline < now)
    status = "Unconfirmed (deadline lewat, masih di index)" if closed else "Appears open (public ATS index)"
    recommendation = "Skip" if geo == "Ineligible" or score < 55 else "Review"
    if re.search(r"\bjunior\b|entry.level|internship", job.title, re.I):
        recommendation = "Skip"
        flags.append("Scope judul junior/entry; di luar target senior")
    if closed:
        flags.append("Deadline lewat bertentangan dengan listing aktif; verifikasi reopening")
    if not job.apply_url:
        flags.append("Direct apply URL tidak tersedia; baca instruksi pada posting")
    return {"score": score, "technical": int(tech * 100 + .5), "components": components,
            "geography": geo, "arrangement": arrangement, "evidence": evidence, "matched": matched,
            "mentions_unproven": gaps, "flags": flags, "freshness": freshness, "status": status,
            "recommendation": recommendation, "salary_provenance": "Employer-published; applicability/basis unverified" if job.salary != "Unknown" else "Unknown"}


def priority(row: dict):
    a = row["assessment"]
    return (a["recommendation"] == "Skip", {"Verified": 0, "Likely": 1, "Unclear": 2, "Ineligible": 3}[a["geography"]],
            -a["score"], a["freshness"] != "7 hari", row["job"]["company"], row["job"]["title"])
