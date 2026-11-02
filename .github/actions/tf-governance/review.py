#!/usr/bin/env python3
"""Post scan findings on added lines in the PR diff, not as issue comments."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, unquote


def api(endpoint, payload=None, paginate=False, method=None):
    args = ["gh", "api", endpoint]
    if paginate:
        args += ["--paginate", "--slurp"]
    if payload is not None:
        args += ["--method", method or "POST", "--input", "-"]
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def added_lines(patch):
    added = set()
    line = None
    for entry in patch.splitlines():
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", entry)
        if hunk:
            line = int(hunk[1])
        elif line is not None and entry.startswith("+"):
            added.add(line)
            line += 1
        elif line is not None and entry.startswith(" "):
            line += 1
    return added


def finding_line(text, address, attribute, changed):
    # This POC handles literal Terraform resource blocks. Skip heredocs and
    # ambiguous declarations rather than putting a finding on unrelated code.
    if "<<" in text:
        return None
    tokens = r'(?P<string>"(?:\\.|[^"\\])*")|(?P<comment>//[^\n]*|\#[^\n]*|/\*[\s\S]*?\*/)'
    code = re.sub(tokens, lambda m: re.sub(r"[^\n]", " ", m[0]) if m["comment"] else m[0], text)
    parts = address.split(".")
    if len(parts) != 2:
        return None
    pattern = rf'(?m)^[ \t]*resource\s+"{re.escape(parts[0])}"\s+"{re.escape(parts[1])}"\s*\{{'
    matches = list(re.finditer(pattern, code))
    if len(matches) != 1:
        return None

    def block_end(opening):
        depth = 0
        braces = r'"(?:\\.|[^"\\])*"|[{}]'
        for token in re.finditer(braces, code[opening:]):
            if token[0] == "{":
                depth += 1
            elif token[0] == "}":
                depth -= 1
                if depth == 0:
                    return opening + token.end()
        return None

    start = matches[0].start()
    end = block_end(matches[0].end() - 1)
    if end is None:
        return None
    first = code.count("\n", 0, start) + 1
    last = code.count("\n", 0, end) + 1
    candidates = sorted(line for line in changed if first <= line <= last)
    if not candidates:
        return None
    lines = code.splitlines()
    attr = re.escape(attribute.split(".")[0])
    for match in re.finditer(rf"(?m)^[ \t]*{attr}[ \t]*(=|\{{)", code[start:end]):
        offset = start + match.start()
        line = code.count("\n", 0, offset) + 1
        if line in candidates:
            return line
        if match[1] == "{":
            close = block_end(start + match.end() - 1)
            if close is not None:
                block_last = code.count("\n", 0, close) + 1
                nested = [n for n in candidates if line <= n <= block_last]
                if nested:
                    return next((n for n in nested if "0.0.0.0/0" in lines[n - 1] or "::/0" in lines[n - 1]), nested[0])
    return candidates[0]


def finding_marker(path, items):
    # A finding survives reruns, merges, line shifts, and policy text updates.
    # The PR endpoint already scopes identity, so neither head SHA nor line belongs here.
    findings = sorted({(item["ruleId"], item["properties"]["resource"], item["properties"]["attribute"])
                       for item in items})
    identity = json.dumps([path, findings], separators=(",", ":"))
    return "<!-- stackgen-governance-inline:v2:" + hashlib.sha256(identity.encode()).hexdigest() + " -->"


def finding_body(marker, items):
    body = marker + "\n**StackGen governance finding**\n\n"
    for item in sorted(items, key=lambda item: (item["ruleId"], item["message"]["text"])):
        text = item["message"]["text"]
        for old, new in [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ("@", "&#64;"), ("`", "&#96;")]:
            text = text.replace(old, new)
        body += "\n".join("> " + line for line in text.splitlines()) + "\n\n"
    return body[:60000]


def legacy_content(body):
    # Old markers contain an opaque hash of the SHA and line. Match their owned
    # finding content once, independent of paragraph order, then migrate in place.
    content = re.sub(r"^<!-- stackgen-governance-inline:[^>]*-->\s*", "", body)
    return sorted({paragraph.strip() for paragraph in content.split("\n\n") if paragraph.strip()})


def plan_comments(grouped, existing):
    managed = [comment for comment in existing
               if comment["user"]["login"] == "github-actions[bot]"
               and not comment.get("in_reply_to_id")
               and comment["body"].startswith("<!-- stackgen-governance-inline:")]
    comments, updates, planned = [], [], set()
    for (path, line), items in grouped.items():
        marker = finding_marker(path, items)
        if marker in planned:
            continue
        planned.add(marker)
        body = finding_body(marker, items)
        match = next((comment for comment in managed if comment["path"] == path and marker in comment["body"]), None)
        if match is None:
            match = next((comment for comment in managed if comment["path"] == path
                          and legacy_content(comment["body"]) == legacy_content(body)), None)
        if match is not None:
            if match["body"] != body:
                updates.append({"id": match["id"], "body": body})
        else:
            comments.append({"path": path, "line": line, "side": "RIGHT", "body": body})
    return comments, updates


def main():
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    report = (workspace / os.environ.get("OUTPUT_FILE", "reports/scan-results.json")).resolve()
    if not report.is_relative_to(workspace):
        raise ValueError("Report path escapes checkout")
    sarif = report.with_suffix(".sarif") if report.suffix == ".json" else Path(str(report) + ".sarif")
    if not sarif.is_file():
        print("No SARIF findings available for inline comments.")
        return
    findings = json.loads(sarif.read_text())["runs"][0]["results"]
    if not findings:
        print("No governance violations to comment on.")
        return
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    number = event["pull_request"]["number"]
    head = os.environ["PR_HEAD_SHA"]
    repo = os.environ["GITHUB_REPOSITORY"]
    endpoint = f"repos/{repo}/pulls/{number}"
    if api(endpoint)["head"]["sha"] != head:
        print("Skipping inline comments for an outdated PR commit.")
        return
    files = {f["filename"]: f for page in api(endpoint + "/files?per_page=100", paginate=True) for f in page}
    contents = {}
    grouped = {}
    for finding in findings:
        for location in finding.get("locations", []):
            path = location["physicalLocation"]["artifactLocation"]["uri"]
            # SARIF URIs are escaped; GitHub file paths are not.
            path = unquote(path)
            changed = files.get(path, {})
            if not path.endswith(".tf") or not changed.get("patch") or changed.get("status") == "removed":
                continue
            if path not in contents:
                content = api(f"repos/{repo}/contents/{quote(path, safe='/')}?ref={head}")
                contents[path] = base64.b64decode(content["content"]).decode()
            properties = finding["properties"]
            line = finding_line(contents[path], properties["resource"], properties["attribute"], added_lines(changed["patch"]))
            if line is not None:
                grouped.setdefault((path, line), []).append(finding)
                break

    existing = [c for page in api(endpoint + "/comments?per_page=100", paginate=True) for c in page]
    comments, updates = plan_comments(grouped, existing)
    if not comments and not updates:
        print("No new findings on changed lines; findings remain in the PR summary.")
        return
    payload = {"commit_id": head, "event": "COMMENT", "comments": comments[:30]}
    if "--dry-run" in sys.argv:
        print(json.dumps({**payload, "updates": updates[:30]}, indent=2))
        return
    if api(endpoint)["head"]["sha"] != head:
        print("Skipping inline comments because the PR head changed.")
        return
    for update in updates[:30]:
        api(f"repos/{repo}/pulls/comments/{update['id']}", {"body": update["body"]}, method="PATCH")
    if updates:
        print(f"Updated {min(len(updates), 30)} existing governance finding comments.")
    if comments:
        review = api(endpoint + "/reviews", payload)
        print(f"Posted {len(payload['comments'])} inline governance comments: {review['html_url']}")


if __name__ == "__main__":
    main()
