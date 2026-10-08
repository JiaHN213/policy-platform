import pytest
from bs4 import BeautifulSoup
from ingestion.documents import readable_html_text
from ingestion.text_layout import normalize_extracted_text


def html_text(value):
    return readable_html_text(BeautifulSoup(value, "html.parser"))


def test_html_source_indentation_is_not_a_paragraph_break():
    assert html_text("<p>支持供水\n  <span>企业</span>\n  开展设备更新。</p><p>第二段。</p>") == (
        "支持供水企业开展设备更新。\n\n第二段。"
    )


def test_html_keeps_real_breaks_and_excludes_comments():
    assert html_text("<p>第一行<br>第二行<!--不属于正文--></p>") == "第一行\n第二行"


def test_html_table_cell_paragraphs_stay_in_their_row():
    text = html_text(
        "<table><tr><th>项目</th><th>金额</th></tr>"
        "<tr><td><p>供水工程</p><p>一期</p></td><td><p>529.2563 万元</p></td></tr>"
        "<tr><td>污水工程</td><td></td></tr></table>"
    )
    assert text.splitlines() == ["项目 | 金额", "供水工程 一期 | 529.2563 万元", "污水工程 |"]


def test_html_merged_cells_keep_empty_column_positions():
    text = html_text(
        '<table><tr><td rowspan="2">供水</td><td>一期</td><td>50</td></tr>'
        '<tr><td>二期</td><td>60</td></tr><tr><td colspan="2">合计</td><td>110</td></tr></table>'
    )
    assert text.splitlines() == ["供水 | 一期 | 50", "| 二期 | 60", "合计 | | 110"]


def test_browser_generated_list_numbers_and_reset_are_preserved():
    text = html_text('<ol start="3"><li>供水</li><li value="7">排水</li><li>污水</li></ol>')
    assert [line for line in text.splitlines() if line] == ["3. 供水", "7. 排水", "8. 污水"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("支持符合相关要求的供水企业依法开展设备\n更新和节能改造。", "支持符合相关要求的供水企业依法开展设备更新和节能改造。"),
        ("Eligible water treatment\ncompanies may apply.", "Eligible water treatment companies may apply."),
        ("Support for eligible water-\nsaving equipment.", "Support for eligible water-saving equipment."),
        ("一、支持符合相关要求的供水企业实施设备更新\n具体安排如下。", "一、支持符合相关要求的供水企业实施设备更新\n具体安排如下。"),
        ("项目名称及申报主体应按照本通知规定填写\n  另一个独立段落。", "项目名称及申报主体应按照本通知规定填写\n另一个独立段落。"),
        ("eligible company name  amount\nwater company  500", "eligible company name amount\nwater company 500"),
        ("供水项目及设备更新实施方案 | 500\n污水项目 | 300", "供水项目及设备更新实施方案 | 500\n污水项目 | 300"),
    ],
)
def test_pdf_wrap_repair_preserves_boundaries_words_and_figures(source, expected):
    assert normalize_extracted_text(source, join_wrapped=True) == expected
