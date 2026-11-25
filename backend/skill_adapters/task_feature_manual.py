"""Run the installed merger/cleaner on uploaded, self-contained HTML documents."""
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

# Imported modules are the reviewed server installation, selected by the caller.
STATIC_TAGS = set(("html body title main article section div span p h1 h2 h3 h4 h5 h6 "
                   "a img figure figcaption ol ul li dl dt dd table caption thead tbody tfoot "
                   "tr th td br hr blockquote pre code strong b em i u s small sup sub "
                   "details summary kbd mark time").split())


def prepare_source(path, merger):
    parser = merger.Parser()
    parser.feed(path.read_text(encoding="utf-8-sig"))
    for node in parser.root.walk():
        if node.tag not in STATIC_TAGS | merger.DISCARD | {"iframe"}:
            node.tag = "div"
        attrs = node.attrs
        node.attrs = {k: v for k, v in attrs.items() if k in ("id", "class", "alt", "title", "colspan", "rowspan")}
        if node.tag == "a":
            url = (attrs.get("href") or "").strip()
            if url.startswith("#") or urlsplit(url).scheme.lower() in ("https", "http"):
                node.attrs["href"] = url
        if node.tag == "img":
            src = attrs.get("src") or ""
            if not src.startswith("data:image/"):
                raise ValueError("截图须内嵌在 HTML 文件中，请重新导出单文件 HTML")
            node.attrs["src"] = src
        if node.tag == "iframe" and (attrs.get("src") or "").startswith("https://"):
            node.attrs["src"] = attrs["src"]
    if not any(next(parser.root.walk(tag), None) for tag in ("article", "main", "body")):
        parser.root.children = [merger.Element("body", children=parser.root.children)]
    path.write_text("".join(n.render() for n in parser.root.children if isinstance(n, merger.Element)), encoding="utf-8")


def main(skill_dir, manifest):
    sys.path.insert(0, str(Path(skill_dir) / "scripts"))
    import merge_reports
    import clean_merged_html

    sources = [Path(p) for p in json.loads(Path(manifest).read_text(encoding="utf-8"))]
    for path in sources:
        try:
            prepare_source(path, merge_reports)
        except ValueError as exc:
            raise ValueError(f"{path.name}：{exc}") from None
    output = Path(manifest).parent / "manual.html"
    merge_reports.merge(sources, output)
    html, _ = clean_merged_html.clean(output.read_text(encoding="utf-8"))
    if clean_merged_html.verify(html):
        raise ValueError("合并后的 HTML 结构校验失败")
    output.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except ValueError as error:
        # The web process maps these to bounded user-facing messages.
        print(str(error)[:240], file=sys.stderr)
        sys.exit(2)
