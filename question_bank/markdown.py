import bleach
import markdown
import re


ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p", "br", "pre", "code", "h1", "h2", "h3", "h4", "ul", "ol", "li",
    "blockquote", "hr", "strong", "em",
}
ALLOWED_ATTRIBUTES = {"a": ["href", "title"], "code": ["class"]}


MATH_PATTERN = re.compile(r"(\$\$.*?\$\$|\$[^$\n]+\$|\\\(.*?\\\)|\\\[.*?\\\])", re.DOTALL)


def render_markdown(source):
    formulas = []

    def protect(match):
        formulas.append(match.group(0))
        return f"MATHFORMULA{len(formulas) - 1}ENDMATHFORMULA"

    protected = MATH_PATTERN.sub(protect, source or "")
    rendered = markdown.markdown(protected, extensions=["extra", "sane_lists"])
    cleaned = bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols={"http", "https", "mailto"},
        strip=True,
    )
    for index, formula in enumerate(formulas):
        cleaned = cleaned.replace(f"MATHFORMULA{index}ENDMATHFORMULA", formula)
    return cleaned
