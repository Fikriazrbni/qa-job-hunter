from __future__ import annotations

import html
import json
import os
from pathlib import Path

from .model import safe_url
from .screen import priority


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def md_text(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def eligible(row):
    return row["assessment"]["recommendation"] != "Skip" and row["kind"] != "not_listed"


def display_salary(value: str) -> str:
    try:
        ranges = json.loads(value)
    except (ValueError, TypeError):
        return value
    if isinstance(ranges, list) and ranges and all(isinstance(r, dict) and 'min_cents' in r and 'max_cents' in r for r in ranges):
        result = []
        for r in ranges:
            low = r['min_cents'] / 100
            high = r['max_cents'] / 100
            result.append(f"{r.get('currency_type', '')} {low:,.0f}–{high:,.0f} · {r.get('title', '')}")
        return '; '.join(result) + ' (periode/gross-net belum dikonfirmasi)'
    return value


def render_rows(rows):
    output = []
    for row in rows:
        j, a = row["job"], row["assessment"]
        link = safe_url(j["apply_url"] or j["url"])
        link_html = f'<a href="{html.escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">Posting / apply</a>' if link else "URL tidak tersedia"
        evidence = " · ".join(a["evidence"])
        flags = "<br>".join(html.escape(x) for x in a["flags"])
        details = f'<details><summary>Bukti, match dan gap</summary><p>{html.escape(evidence)}</p><p>Match: {html.escape(", ".join(a["matched"]) or "N/A")}</p><p>Disebut JD, belum terbukti CV (bisa optional/alternatif): {html.escape(", ".join(a["mentions_unproven"]) or "Tidak terdeteksi")}</p><p>{flags}</p><p>Komponen: {html.escape(json.dumps(a["components"]))}</p></details>'
        output.append(f'<tr><td><strong>{html.escape(j["company"])}</strong><br>{html.escape(j["title"])}</td><td>{a["score"]}<br><small>Teknis {a["technical"]}</small></td><td>{html.escape(a["geography"])}<br>{html.escape(j["location"])}</td><td>{html.escape(a["status"])}<br>{html.escape(str(j["published_at"] or "Not available"))}<br><small>{html.escape(j["date_basis"])}; {html.escape(a["freshness"])}</small></td><td>{html.escape(display_salary(j["salary"]))}<br><small>{html.escape(a["salary_provenance"])}</small></td><td>{html.escape(row["kind"])} / {html.escape(a["recommendation"])}<br>{link_html}{details}</td></tr>')
    return "".join(output) or '<tr><td colspan="6">Tidak ada hasil pada kelompok ini.</td></tr>'


def table(rows):
    return '<div class="scroll"><table><thead><tr><th>Perusahaan / posisi</th><th>Skor</th><th>Geografi</th><th>Status / publikasi</th><th>Gaji</th><th>Perubahan / keputusan</th></tr></thead><tbody>' + render_rows(rows) + '</tbody></table></div>'


def write_reports(folder: Path, result: dict, limit: int):
    changed = sorted([r for r in result["events"] if eligible(r)], key=priority)
    current = sorted([r for r in result["active"] if eligible(r) and r["checked_this_run"]], key=priority)
    stale = [r for r in result["active"] if eligible(r) and not r["checked_this_run"]]
    excluded = sorted([r for r in result["active"] if r["assessment"]["recommendation"] == "Skip"], key=priority)
    unavailable = [r for r in result["events"] if r["kind"] == "not_listed"]
    warning = "Rules-only, tanpa AI. Skor adalah kecocokan kata kunci, bukan probabilitas diterima. Semua hasil tetap Review; baca sumber sebelum melamar. Verified hanya cakupan geografis berdasarkan aturan eksplisit, bukan jaminan authorization/entity."
    errors = "".join(f'<li><strong>{html.escape(x["source"])}</strong>: {html.escape(x["error"])}</li>' for x in result["sources"] if x.get("error")) or '<li>Tidak ada source error.</li>'
    heading = f'QA Job Hunter · {result["checked_local"]}'
    document = f'''<!doctype html><html lang="id"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(heading)}</title>
<style>body{{font:15px/1.55 system-ui,sans-serif;max-width:1400px;margin:32px auto;padding:0 20px;color:#17212b;background:#fafbfd}}h1{{font-size:28px}}h2{{margin-top:32px}}.notice{{padding:16px;background:#eaf1fb;border-left:4px solid #305ab5}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:12px;border:1px solid #dbe2ec;vertical-align:top;text-align:left}}th{{background:#edf2f8}}a{{color:#204fa4}}small{{color:#556}}summary{{cursor:pointer;color:#204fa4}}details{{max-width:460px;margin-top:10px}}.scroll{{overflow:auto}}.meta{{color:#46576b}}</style>
<h1>{html.escape(heading)}</h1><p class="meta">Run {result["run_id"]} · {result["outcome"]} · {result["summary"]["successful_sources"]}/{len(result["sources"])} sumber berhasil · {result["summary"]["screened"]} role QA disaring</p>
<p class="notice">{html.escape(warning)}<br>Cakupan hanya watchlist config.json, bukan pencarian seluruh internet atau feed LinkedIn. Baru ditemukan ≠ baru dipublikasikan.</p>
<h2>Baru / perubahan yang perlu ditinjau ({len(changed)})</h2>{table(changed[:limit])}
<h2>Snapshot kandidat yang diperiksa pada run ini ({len(current)})</h2><p>Bagian ini dapat berisi hasil lama; tidak dihitung sebagai temuan baru.</p>{table(current[:limit])}
<h2>Sumber yang bermasalah</h2><ul>{errors}</ul>
<details><summary>Data lama, belum diperiksa ulang pada run ini ({len(stale)})</summary>{table(stale)}</details>
<details><summary>Tidak lagi tercantum pada index ({len(unavailable)})</summary><p>Belum tentu Closed; gagal mengambil sumber tidak pernah dianggap lowongan tutup.</p>{table(unavailable)}</details>
<details><summary>Tersaring / di luar target ({len(excluded)})</summary>{table(excluded)}</details>
<p>Tidak ada aplikasi, CV atau pesan yang dikirim. Full data tersedia dalam latest.json.</p></html>'''
    markdown = [f"# {heading}", "", warning, "", f"Outcome: {result['outcome']}. Cakupan: watchlist, bukan seluruh internet/LinkedIn.", "", "## Baru / perubahan", "", "| Company / title | Score | Geography | Published / basis | Decision | Link |", "| --- | ---: | --- | --- | --- | --- |"]
    for row in changed[:limit]:
        j, a = row["job"], row["assessment"]
        markdown.append(f"| {md_text(j['company']+' — '+j['title'])} | {a['score']} | {md_text(a['geography']+'; '+j['location'])} | {md_text(str(j['published_at'] or 'Not available')+'; '+j['date_basis'])} | {row['kind']} / {a['recommendation']} | {md_text(j['apply_url'] or j['url'])} |")
    if not changed:
        markdown += ["", "Tidak ada kandidat baru/perubahan yang lolos filter awal."]
    markdown += ["", "## Kesehatan sumber", ""]
    markdown += [f"- {s['source']}: {s.get('error') or str(s['count'])+' posting diperiksa'}" for s in result["sources"]]
    stem = f"run-{result['run_id']:05d}"
    data = json.dumps(result, indent=2, ensure_ascii=False)
    for name, content in [(stem + ".html", document), (stem + ".json", data), (stem + ".md", "\n".join(markdown)),
                          ("latest.html", document), ("latest.json", data), ("latest.md", "\n".join(markdown))]:
        atomic_write(folder / name, content)
    return folder / "latest.html"
