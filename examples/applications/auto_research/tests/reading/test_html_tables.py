"""html_tables: panel captions stay with their own rows, math is read once."""

from auto_research.tasks.reading.reader import html_tables

MATH = '<math alttext="4.83\\pm 0.066"><mn>4.83</mn><mo>±</mo><annotation>4.83\\pm 0.066</annotation></math>'


def test_panels_keep_their_captions_and_math_is_read_once():
    html = f"""<figure class="ltx_table"><figcaption>Table 1: Errors on (a) CIFAR-10 and (b) CIFAR-100.</figcaption>
    <figure class="ltx_figure_panel"><figcaption>(a) CIFAR-10</figcaption>
    <table><tr><td>No Mixup</td><td>{MATH}</td></tr></table></figure>
    <figure class="ltx_figure_panel"><table><tr><td>No Mixup</td><td>21.10</td></tr></table>
    <figcaption>(b) CIFAR-100</figcaption></figure></figure>"""
    [table] = html_tables(html)
    lines = table.splitlines()
    assert lines[0] == "Table 1: Errors on (a) CIFAR-10 and (b) CIFAR-100."
    assert lines[1:] == ["[(a) CIFAR-10]", "No Mixup | 4.83\\pm 0.066", "[(b) CIFAR-100]", "No Mixup | 21.10"]


def test_side_by_side_tables_without_an_outer_caption_are_separate():
    html = """<figure class="ltx_table">
    <figure class="ltx_figure_panel"><figcaption>Table 4: Adversarial accuracy.</figcaption>
    <table><tr><td>Acc.</td><td>8.2</td></tr></table></figure>
    <figure class="ltx_figure_panel"><figcaption>Table 5: Training time.</figcaption>
    <table><tr><td>Time</td><td>0.83</td></tr></table></figure></figure>"""
    assert html_tables(html) == ["Table 4: Adversarial accuracy.\nAcc. | 8.2", "Table 5: Training time.\nTime | 0.83"]
