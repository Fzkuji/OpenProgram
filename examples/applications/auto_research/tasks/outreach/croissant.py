"""Croissant metadata for a released dataset, as NeurIPS Datasets & Benchmarks and
Hugging Face dataset cards ask for it: a draft from the paper's datasheet, and a check.

croissant_draft fills workflow/croissant.json from workflow/datasheet.md and the files
it names (sha256 computed for local files, column fields read from CSV/TSV/JSONL
headers), leaving "TODO" strings where the datasheet does not say. croissant_check
validates the file: with the `mlcroissant` package when it is importable, else in plain
code against the essentials of the Croissant 1.0 spec; the Responsible-AI fields
(Croissant RAI) are always checked in plain code. No model calls.
"""

import csv
import hashlib
import json
import re
from pathlib import Path

from auto_research import stages
from auto_research.checks import fields
from auto_research.trail import Trail

from ..proofread.workflow import paper_title
from . import common

CONTEXT = {
    "@language": "en", "@vocab": "https://schema.org/", "sc": "https://schema.org/",
    "cr": "http://mlcommons.org/croissant/", "rai": "http://mlcommons.org/croissant/RAI/",
    "dct": "http://purl.org/dc/terms/", "citeAs": "cr:citeAs", "column": "cr:column",
    "conformsTo": "dct:conformsTo", "data": {"@id": "cr:data", "@type": "@json"},
    "dataType": {"@id": "cr:dataType", "@type": "@vocab"}, "examples": {"@id": "cr:examples", "@type": "@json"},
    "extract": "cr:extract", "field": "cr:field", "fileProperty": "cr:fileProperty", "fileObject": "cr:fileObject",
    "fileSet": "cr:fileSet", "format": "cr:format", "includes": "cr:includes", "isLiveDataset": "cr:isLiveDataset",
    "jsonPath": "cr:jsonPath", "key": "cr:key", "md5": "cr:md5", "parentField": "cr:parentField", "path": "cr:path",
    "recordSet": "cr:recordSet", "references": "cr:references", "regex": "cr:regex", "repeated": "cr:repeated",
    "replace": "cr:replace", "separator": "cr:separator", "source": "cr:source", "subField": "cr:subField",
    "transform": "cr:transform",
}
CONFORMS = "http://mlcommons.org/croissant/1.0"
CR, SC, RAI = "http://mlcommons.org/croissant/", "https://schema.org/", "http://mlcommons.org/croissant/RAI/"
DATASET_REQUIRED = ("name", "description", "license", "url")
DATASET_RECOMMENDED = ("creator", "datePublished", "version", "keywords", "citeAs")
# The RAI fields the NeurIPS Datasets & Benchmarks track asks every dataset to answer, then the recommended rest.
RAI_REQUIRED = ("dataCollection", "dataBiases", "personalSensitiveInformation", "dataUseCases", "dataLimitations")
RAI_RECOMMENDED = ("dataCollectionType", "dataSocialImpact", "dataPreprocessingProtocol",
                   "dataAnnotationProtocol", "dataReleaseMaintenancePlan")
FORMATS = {".csv": "text/csv", ".tsv": "text/tab-separated-values", ".json": "application/json",
           ".jsonl": "application/jsonlines", ".parquet": "application/x-parquet", ".zip": "application/zip",
           ".gz": "application/gzip", ".tar": "application/x-tar", ".txt": "text/plain", ".png": "image/png",
           ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".wav": "audio/wav", ".mp3": "audio/mpeg",
           ".mp4": "video/mp4", ".npy": "application/octet-stream", ".pt": "application/octet-stream"}
RAI_KEYS = {f"rai:{k}" for k in RAI_REQUIRED + RAI_RECOMMENDED}
PLAIN_COVERED = RAI_KEYS | set(DATASET_REQUIRED) | set(DATASET_RECOMMENDED) | {"conformsTo", "distribution", "recordSet"}
MOST_FILES = 200
TODO = "TODO"
PATH_WORD = re.compile(r"`([^`\s]+)`|^\s*[-*]\s+(\S+)\s*$", re.MULTILINE)
URL = re.compile(r"https?://[^\s)>`\]]+")


def croissant_check(paper_dir: str, path: str = "", out_dir: str = "") -> str:
    """Validate the dataset's Croissant metadata (JSON-LD).

    path is the file, relative to paper_dir or absolute; empty reads
    workflow/croissant.json. With the mlcroissant package importable its validation
    (mlcroissant.Dataset) checks the core vocabulary; otherwise plain code checks the
    Croissant 1.0 essentials: @context with the cr and sc namespaces, @type sc:Dataset,
    conformsTo, name, description, license, url, a distribution of cr:FileObject (with
    contentUrl, encodingFormat and sha256 or md5) and cr:FileSet (with containedIn,
    includes and encodingFormat), record sets whose fields have a dataType and a source
    naming a known file or record set, unique @ids, and the sha256 of local files. The
    Croissant RAI fields the NeurIPS D&B track asks for (dataCollection, dataBiases,
    personalSensitiveInformation, dataUseCases, dataLimitations) are required in every
    case; dataCollectionType, dataSocialImpact, dataPreprocessingProtocol,
    dataAnnotationProtocol and dataReleaseMaintenancePlan are notes. A value still
    "TODO" counts as missing. FAIL on a missing required field, PASS_WITH_NOTES on notes.
    Output in out_dir/<timestamp>/ (default <paper_dir>/drafts/croissant_check):
    report.md, croissant.json (the findings), run.json.
    """
    paper = common.repo(paper_dir)
    file = Path(path).expanduser() if path else paper / "workflow" / "croissant.json"
    if not file.is_absolute():
        file = paper / file
    out = common.run_folder(paper, out_dir, "croissant_check")
    trail = Trail("croissant_check", {"paper_dir": str(paper), "path": str(file), "out_dir": out_dir}, {})
    found, validator = [], "plain"
    data = None
    if not file.is_file():
        found.append(common.finding("error", file.name, "Croissant 文件不存在。",
                                    "运行 croissant_draft 从 workflow/datasheet.md 起草，再补上 TODO。"))
    else:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            found.append(common.finding("error", f"{file.name}:{e.lineno}", f"不是合法 JSON：{e.msg}。", "修正 JSON 语法。"))
    if isinstance(data, dict):
        mlc = load_mlcroissant()
        if mlc is not None:
            validator = "mlcroissant"
            found += mlcroissant_findings(mlc, file)
            found += todo_findings(data, RAI_KEYS)
        else:
            found += core_findings(data, file, paper)
        found += rai_findings(data)
    elif data is not None:
        found.append(common.finding("error", file.name, "顶层不是 JSON 对象。", "Croissant 文件是一个 sc:Dataset 对象。"))
    trail.step("validate", validator=validator, errors=sum(f["level"] == "error" for f in found),
               notes=sum(f["level"] == "note" for f in found))
    errors = [f for f in found if f["level"] == "error"]
    record = {"status": common.status(len(errors), len(found) - len(errors)), "file": str(file),
              "validator": validator, "findings": found}
    lines = ["# Croissant 元数据检查", "", f"文件：`{file}`　校验：{validator}　结论：{record['status']}", ""]
    result = common.save(out, trail, "croissant", record, "\n".join(lines + common.findings_md(found)))
    return json.dumps({**result, "validator": validator, "errors": len(errors),
                       "notes": len(found) - len(errors)}, ensure_ascii=False)


def croissant_draft(paper_dir: str, out_dir: str = "") -> str:
    """Draft Croissant metadata from workflow/datasheet.md and the dataset files it names.

    The datasheet's License and Location fields, optional Name, Description, URL,
    Creator, Version and Date fields, and its parts (Motivation, Collection process,
    Preprocessing, Uses, Maintenance, and any part about biases, sensitive information,
    limitations or social impact) fill the dataset and RAI properties. Files: Location
    when it is a local file or folder (up to 200 files), paths in backticks or list items
    of the Distribution part that exist, and URLs there. A local file gets its sha256 and
    encodingFormat; a CSV/TSV/JSONL file a record set with one field per column, its
    type guessed from the first row. Anything unknown is the string "TODO". Writes
    workflow/croissant.json only when it does not exist; the draft always goes to
    out_dir/<timestamp>/croissant.json (default <paper_dir>/drafts/croissant_draft) with
    report.md listing the TODOs. Status PASS_WITH_NOTES while TODOs remain.
    """
    paper = common.repo(paper_dir)
    sheet_path = paper / "workflow" / "datasheet.md"
    sheet = sheet_path.read_text(encoding="utf-8") if sheet_path.is_file() else stages.template("datasheet.md")
    values, parts = {k.lower(): v for k, v in fields(sheet).items()}, stages.parts(sheet)
    out = common.run_folder(paper, out_dir, "croissant_draft")
    trail = Trail("croissant_draft", {"paper_dir": str(paper), "out_dir": out_dir}, {})
    files = dataset_files(paper, values.get("location", ""), parts.get("Distribution", ""))
    trail.step("read", datasheet=sheet_path.is_file(), files=[f["name"] for f in files])
    doc = draft(paper, values, parts, files)
    todo = todos(doc)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    (out / "croissant.json").write_text(text, encoding="utf-8")
    target = paper / "workflow" / "croissant.json"
    written = not target.exists()
    if written:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    trail.step("draft", files=len(files), record_sets=len(doc["recordSet"]), todo=todo, wrote_workflow=written)
    record = {"status": "PASS_WITH_NOTES" if todo or not sheet_path.is_file() else "PASS", "todo": todo,
              "files": files, "workflow_file": str(target) if written else "", "draft": str(out / "croissant.json")}
    lines = ["# Croissant 草稿", "", f"结论：{record['status']}", "",
             f"草稿：`{out / 'croissant.json'}`" + (f"，已写入 `{target}`" if written else
                                                   f"；`{target}` 已存在，没有覆盖，需要时手动合并"), ""]
    if not sheet_path.is_file():
        lines += ["> 注意：没有 workflow/datasheet.md，只按模板起草。", ""]
    lines += [f"数据文件 {len(files)} 个：", ""] + [f"- `{f['contentUrl']}`" + (f"（sha256 {f['sha256'][:12]}…）"
                                                                         if f.get("sha256") else "") for f in files]
    lines += ["", "## 还要补的 TODO", ""] + ([f"- `{t}`" for t in todo] or ["无。"]) + [""]
    result = common.save(out, trail, "draft", record, "\n".join(lines), croissant=out / "croissant.json")
    return json.dumps({**result, "croissant": str(out / "croissant.json"),
                       "workflow_file": record["workflow_file"], "todo": len(todo)}, ensure_ascii=False)


# Validation -------------------------------------------------------------------------

def load_mlcroissant():
    try:
        import mlcroissant
    except ImportError:
        return None
    return mlcroissant


def mlcroissant_findings(mlc, file: Path) -> list[dict]:
    """mlcroissant's own validation: errors raise, warnings are kept on the metadata's issues."""
    try:
        dataset = mlc.Dataset(jsonld=str(file))
    except Exception as e:      # mlcroissant raises ValidationError with every error in its message
        lines = [line.strip(" -") for line in str(e).splitlines() if line.strip(" -")] or [repr(e)]
        return [common.finding("error", "mlcroissant", line, "按 Croissant 1.0 规范补齐或改正。") for line in lines[:50]]
    issues = getattr(getattr(getattr(dataset, "metadata", None), "ctx", None), "issues", None)
    return [common.finding("note", "mlcroissant", str(w)) for w in list(getattr(issues, "warnings", []) or [])[:50]]


def term(obj: dict, name: str, prefix: str = "", iri: str = ""):
    """A property under its plain name, its prefixed name or its full IRI."""
    for key in (name, f"{prefix}:{name}" if prefix else None, f"{iri}{name}" if iri else None):
        if key and key in obj:
            return obj[key]
    return None


def empty(value) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return True
    if isinstance(value, str):
        return TODO in value
    return False


def as_list(value) -> list:
    return value if isinstance(value, list) else [] if value is None else [value]


def types(obj: dict) -> list[str]:
    return [str(t) for t in as_list(obj.get("@type"))]


def is_type(obj: dict, name: str, prefix: str, iri: str) -> bool:
    return any(t in (f"{prefix}:{name}", f"{iri}{name}", name) for t in types(obj))


def ref_id(value) -> str:
    if isinstance(value, dict):
        return str(value.get("@id", ""))
    return str(value or "")


def core_findings(data: dict, file: Path, paper: Path) -> list[dict]:
    """The Croissant 1.0 essentials, in plain code."""
    found = []

    def err(where, problem, fix=""):
        found.append(common.finding("error", where, problem, fix))

    def note(where, problem, fix=""):
        found.append(common.finding("note", where, problem, fix))

    ctx = data.get("@context")
    if not isinstance(ctx, dict):
        err("@context", "缺少 @context 对象。", "用 Croissant 规范附录里的完整 @context。")
    else:
        if ctx.get("cr") != CR:
            err("@context.cr", f"cr 应指向 {CR}。")
        if ctx.get("sc") != SC and ctx.get("@vocab") != SC:
            err("@context.sc", f"sc 或 @vocab 应指向 {SC}。")
        if any(str(k).startswith("rai:") for k in data) and ctx.get("rai") != RAI:
            err("@context.rai", f"用了 rai: 字段，但 @context 没有 rai → {RAI}。")
    if not is_type(data, "Dataset", "sc", SC) and "schema:Dataset" not in types(data):
        err("@type", "顶层 @type 应为 sc:Dataset。")
    conforms = term(data, "conformsTo", "dct", "http://purl.org/dc/terms/")
    if empty(conforms):
        err("conformsTo", "缺少 conformsTo。", f'写 "conformsTo": "{CONFORMS}"。')
    elif not str(conforms).startswith("http://mlcommons.org/croissant/1."):
        err("conformsTo", f"conformsTo 是 {conforms}，不是 Croissant 1.x。", f"改成 {CONFORMS}。")
    for key in DATASET_REQUIRED:
        if empty(term(data, key, "sc", SC)):
            err(key, f"{key} 为空或还是 TODO。", "Croissant 要求这个字段。")
    for key in DATASET_RECOMMENDED:
        value = term(data, key, "sc", SC) if key != "citeAs" else term(data, key, "cr", CR)
        if empty(value):
            note(key, f"建议填写 {key}。")
    ids: dict[str, str] = {}

    def remember(obj: dict, where: str) -> str:
        oid = str(obj.get("@id") or obj.get("name") or "")
        if not oid:
            err(where, "缺少 @id（或 name）。", "给每个文件和字段一个唯一 @id。")
        elif oid in ids:
            err(where, f"@id {oid} 重复（也在 {ids[oid]}）。")
        else:
            ids[oid] = where
        return oid

    files: set[str] = set()
    dist = as_list(term(data, "distribution", "sc", SC))
    if not dist:
        err("distribution", "没有 distribution：数据文件一个都没列。", "为每个文件写 cr:FileObject，或用 cr:FileSet 描述一组文件。")
    for i, item in enumerate(dist):
        where = f"distribution[{i}]"
        if not isinstance(item, dict):
            err(where, "不是对象。")
            continue
        oid = remember(item, where)
        files.add(oid)
        if is_type(item, "FileObject", "cr", CR):
            for key in ("contentUrl", "encodingFormat"):
                if empty(term(item, key, "sc", SC)):
                    err(f"{where}.{key}", f"FileObject {oid} 缺少 {key}。")
            checksum = term(item, "sha256", "sc", SC) or term(item, "md5", "cr", CR)
            if empty(checksum) and empty(item.get("containedIn")):
                err(f"{where}.sha256", f"FileObject {oid} 没有 sha256 或 md5。", "算出文件的 sha256 填上。")
            found += checksum_findings(item, where, file, paper)
        elif is_type(item, "FileSet", "cr", CR):
            if empty(item.get("encodingFormat")):
                err(f"{where}.encodingFormat", f"FileSet {oid} 缺少 encodingFormat。")
            if empty(term(item, "includes", "cr", CR)):
                err(f"{where}.includes", f"FileSet {oid} 缺少 includes（文件的 glob）。")
            if empty(item.get("containedIn")):
                err(f"{where}.containedIn", f"FileSet {oid} 缺少 containedIn（所在的 FileObject）。")
        else:
            err(f"{where}.@type", f"@type 是 {types(item) or '空'}，应为 cr:FileObject 或 cr:FileSet。")
    for i, item in enumerate(dist):
        for parent in as_list(item.get("containedIn")) if isinstance(item, dict) else []:
            if ref_id(parent) and ref_id(parent) not in files:
                err(f"distribution[{i}].containedIn", f"containedIn 指向不存在的 {ref_id(parent)}。")
    sets = as_list(term(data, "recordSet", "cr", CR))
    record_ids = {str(r.get("@id") or r.get("name") or "") for r in sets if isinstance(r, dict)}
    if not sets:
        err("recordSet", "没有 recordSet：记录的结构没有描述。", "为每张表写 cr:RecordSet，每列一个 cr:Field。")
    for i, rs in enumerate(sets):
        where = f"recordSet[{i}]"
        if not isinstance(rs, dict):
            err(where, "不是对象。")
            continue
        rid = remember(rs, where)
        if not is_type(rs, "RecordSet", "cr", CR):
            err(f"{where}.@type", f"@type 应为 cr:RecordSet（{rid}）。")
        fields_ = as_list(term(rs, "field", "cr", CR))
        if not fields_ and empty(rs.get("data")):
            err(f"{where}.field", f"RecordSet {rid} 没有字段。")
        for j, fld in enumerate(fields_):
            found += field_findings(fld, f"{where}.field[{j}]", files, record_ids, remember)
    return found + todo_findings(data, PLAIN_COVERED)


def field_findings(fld, where: str, files: set[str], record_ids: set[str], remember) -> list[dict]:
    if not isinstance(fld, dict):
        return [common.finding("error", where, "字段不是对象。")]
    found = []
    fid = remember(fld, where)
    if not is_type(fld, "Field", "cr", CR):
        found.append(common.finding("error", f"{where}.@type", f"@type 应为 cr:Field（{fid}）。"))
    subs = as_list(term(fld, "subField", "cr", CR))
    if empty(term(fld, "dataType", "cr", CR)) and not subs:
        found.append(common.finding("error", f"{where}.dataType", f"字段 {fid} 缺少 dataType。",
                                    "如 sc:Text、sc:Integer、sc:Float、sc:Boolean、sc:ImageObject。"))
    src = term(fld, "source", "cr", CR)
    if empty(src) and not subs and empty(term(fld, "references", "cr", CR)):
        found.append(common.finding("error", f"{where}.source", f"字段 {fid} 缺少 source。",
                                    "写明来自哪个 fileObject / fileSet 以及 extract（column、jsonPath 或 fileProperty）。"))
    elif isinstance(src, dict):
        named = [ref_id(term(src, k, "cr", CR)) for k in ("fileObject", "fileSet", "recordSet") if term(src, k, "cr", CR)]
        if not named:
            found.append(common.finding("error", f"{where}.source", f"字段 {fid} 的 source 没有指向 fileObject、fileSet 或 recordSet。"))
        for n in named:
            if n not in files | record_ids:
                found.append(common.finding("error", f"{where}.source", f"字段 {fid} 的 source 指向不存在的 {n}。"))
    for k, sub_field in enumerate(subs):
        found += field_findings(sub_field, f"{where}.subField[{k}]", files, record_ids, remember)
    return found


def checksum_findings(item: dict, where: str, file: Path, paper: Path) -> list[dict]:
    """A local contentUrl must exist and match its sha256."""
    url = str(term(item, "contentUrl", "sc", SC) or "")
    want = str(term(item, "sha256", "sc", SC) or "")
    if not url or url.startswith(("http://", "https://")) or TODO in url:
        return []
    local = next((p for p in (file.parent / url, paper / url, paper / "workflow" / url) if p.is_file()), None)
    if local is None:
        return [common.finding("note", f"{where}.contentUrl", f"本地找不到 {url}，没有核对 sha256。")]
    if want and TODO not in want and sha256(local) != want.lower():
        return [common.finding("error", f"{where}.sha256", f"{url} 的 sha256 和文件不符。", "文件改过就重新算 sha256。")]
    return []


def rai_findings(data: dict) -> list[dict]:
    found = []
    for key in RAI_REQUIRED:
        if empty(term(data, key, "rai", RAI)):
            found.append(common.finding("error", f"rai:{key}", f"Responsible AI 字段 rai:{key} 为空或还是 TODO。",
                                        "NeurIPS Datasets & Benchmarks 要求填写；从 datasheet 的对应部分写。"))
    for key in RAI_RECOMMENDED:
        if empty(term(data, key, "rai", RAI)):
            found.append(common.finding("note", f"rai:{key}", f"建议填写 rai:{key}。"))
    return found


def todo_findings(data: dict, covered: set[str]) -> list[dict]:
    """Values still "TODO" under properties no other check reports."""
    return [common.finding("error", p, "还是 TODO。", "补上真实的值。") for p in todos(data)
            if re.split(r"[.\[]", p)[0] not in covered]


def todos(value, where: str = "") -> list[str]:
    """Paths of every string still holding TODO."""
    if isinstance(value, str):
        return [where or "."] if TODO in value else []
    if isinstance(value, dict):
        return [p for k, v in value.items() if k != "@context" for p in todos(v, f"{where}.{k}" if where else k)]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in todos(v, f"{where}[{i}]")]
    return []


# Drafting ---------------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dataset_files(paper: Path, location: str, distribution: str) -> list[dict]:
    """The FileObjects of the files the datasheet names, local ones with their sha256."""
    paths: list[Path] = []
    urls: list[str] = []
    if location.startswith(("http://", "https://")):
        urls.append(location)
    elif location:
        loc = paper / location
        if loc.is_file():
            paths.append(loc)
        elif loc.is_dir():
            paths += sorted(p for p in loc.rglob("*") if p.is_file() and not p.name.startswith("."))[:MOST_FILES]
    for m in PATH_WORD.finditer(distribution):
        word = m.group(1) or m.group(2)
        if word.startswith(("http://", "https://")):
            continue
        p = paper / word
        if p.is_file() and p not in paths:
            paths.append(p)
    urls += [u for u in URL.findall(distribution) if u not in urls]
    out, names = [], set()
    for p in paths:
        rel = p.relative_to(paper).as_posix()
        name = unique(re.sub(r"[^A-Za-z0-9_.-]", "_", rel), names)
        out.append({"@type": "cr:FileObject", "@id": name, "name": name,
                    "contentUrl": f"../{rel}", "encodingFormat": FORMATS.get(p.suffix.lower(), f"{TODO}: media type"),
                    "contentSize": f"{p.stat().st_size} B", "sha256": sha256(p), "_local": str(p)})
    for u in urls:
        if location.startswith(("http://", "https://")) and u == location and paths:
            continue
        base = u.rstrip("/").rsplit("/", 1)[-1] or "remote"
        name = unique(re.sub(r"[^A-Za-z0-9_.-]", "_", base), names)
        out.append({"@type": "cr:FileObject", "@id": name, "name": name, "contentUrl": u,
                    "encodingFormat": FORMATS.get(Path(base).suffix.lower(), f"{TODO}: media type"),
                    "sha256": f"{TODO}: sha256 of the downloaded file"})
    return out


def unique(name: str, taken: set[str]) -> str:
    out, i = name, 2
    while out in taken:
        out, i = f"{name}_{i}", i + 1
    taken.add(out)
    return out


def guess_type(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"-?\d+", value):
        return "sc:Integer"
    if re.fullmatch(r"-?(\d+\.\d*|\.\d+|\d+)(e-?\d+)?", value, re.IGNORECASE):
        return "sc:Float"
    if value.lower() in ("true", "false"):
        return "sc:Boolean"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", value):
        return "sc:Date"
    return "sc:Text"


def columns(path: Path) -> list[tuple[str, str, str]]:
    """(name, dataType, extract key) per column of a CSV/TSV/JSONL file, from its first row."""
    suffix = path.suffix.lower()
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            if suffix in (".csv", ".tsv"):
                reader = csv.reader(f, delimiter="\t" if suffix == ".tsv" else ",")
                head = next(reader, [])
                first = next(reader, [])
                return [(h, guess_type(first[i]) if i < len(first) else "sc:Text", "column") for i, h in enumerate(head) if h]
            if suffix == ".jsonl":
                row = json.loads(f.readline() or "{}")
                return [(k, json_type(v), "jsonPath") for k, v in row.items()] if isinstance(row, dict) else []
    except (OSError, ValueError):
        return []
    return []


def json_type(value) -> str:
    if isinstance(value, bool):
        return "sc:Boolean"
    if isinstance(value, int):
        return "sc:Integer"
    if isinstance(value, float):
        return "sc:Float"
    return "sc:Text"


def record_sets(files: list[dict]) -> list[dict]:
    sets = []
    for f in files:
        cols = columns(Path(f["_local"])) if f.get("_local") else []
        if not cols:
            continue
        rid = Path(f["@id"]).stem
        sets.append({"@type": "cr:RecordSet", "@id": rid, "name": rid, "field": [
            {"@type": "cr:Field", "@id": f"{rid}/{name}", "name": name, "dataType": dtype,
             "source": {"fileObject": {"@id": f["@id"]},
                        "extract": {key: name if key == "column" else f"$.{name}"}}} for name, dtype, key in cols]})
    if not sets:
        first = files[0]["@id"] if files else f"{TODO}: file id"
        sets.append({"@type": "cr:RecordSet", "@id": "records", "name": "records", "field": [
            {"@type": "cr:Field", "@id": "records/TODO", "name": f"{TODO}: field name",
             "dataType": f"{TODO}: sc:Text, sc:Integer, sc:Float, sc:ImageObject...",
             "source": {"fileObject": {"@id": first}, "extract": {"column": f"{TODO}: column"}}}]})
    return sets


def part_like(parts: dict[str, str], *words: str) -> str:
    for name, text in parts.items():
        if text and any(w in name.lower() for w in words):
            return text
    return ""


def para(text: str) -> str:
    return " ".join(text.split()) if text and text.strip() else ""


def draft(paper: Path, values: dict, parts: dict, files: list[dict]) -> dict:
    def pick(value: str, what: str) -> str:
        return value or f"{TODO}: {what}"

    try:
        _, full = common.source(paper)
        title = common.detex(paper_title(full))
    except (ValueError, OSError):
        title = ""
    motivation = para(parts.get("Motivation", ""))
    doc = {
        "@context": CONTEXT, "@type": "sc:Dataset", "conformsTo": CONFORMS,
        "name": pick(values.get("name", "") or title, "dataset name"),
        "description": pick(values.get("description", "") or motivation, "one-paragraph description"),
        "license": pick(values.get("license", ""), "SPDX license, e.g. CC-BY-4.0"),
        "url": pick(values.get("url", "") or (values.get("location", "")
                                              if values.get("location", "").startswith("http") else ""), "dataset URL"),
        "creator": [{"@type": "sc:Person", "name": n.strip()} for n in values.get("creator", "").split(",") if n.strip()]
        or [{"@type": "sc:Person", "name": f"{TODO}: creator"}],
        "datePublished": pick(values.get("date", ""), "YYYY-MM-DD"),
        "version": values.get("version", "") or "1.0.0",
        "rai:dataCollection": pick(para(parts.get("Collection process", "")), "how the data was collected"),
        "rai:dataCollectionType": f"{TODO}: e.g. Web Scraping, Surveys, Experiments, Manual Human Curation",
        "rai:dataPreprocessingProtocol": pick(para(parts.get("Preprocessing", "")), "preprocessing steps"),
        "rai:dataUseCases": pick(para(parts.get("Uses", "")), "intended and out-of-scope uses"),
        "rai:dataBiases": pick(para(part_like(parts, "bias")), "known biases"),
        "rai:personalSensitiveInformation": pick(para(part_like(parts, "sensitive", "personal", "privacy")),
                                                 "personal or sensitive information and how it is handled"),
        "rai:dataLimitations": pick(para(part_like(parts, "limitation")), "known limitations"),
        "rai:dataSocialImpact": pick(para(part_like(parts, "impact")), "social impact"),
        "rai:dataReleaseMaintenancePlan": pick(para(parts.get("Maintenance", "")), "maintenance plan"),
        "distribution": [{k: v for k, v in f.items() if not k.startswith("_")} for f in files]
        or [{"@type": "cr:FileObject", "@id": "data", "name": "data", "contentUrl": f"{TODO}: URL or path",
             "encodingFormat": f"{TODO}: media type", "sha256": f"{TODO}: sha256"}],
        "recordSet": record_sets(files),
    }
    return doc


__all__ = ["croissant_check", "croissant_draft"]
