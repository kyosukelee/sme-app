"""見本の注文書PDFを作る(会社・品目・番号はすべて架空)。

docs/samples/orders の PDF はこのスクリプトで作ったもの。
AI_PROVIDER=fake のときは、この PDF にだけ app/ai_import/fake_answers の答えを返す。

    python scripts/make_sample_orders.py <出力先フォルダ>
"""

import sys
from pathlib import Path

from weasyprint import HTML

STYLE = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "IPAexGothic", "Noto Sans CJK JP", sans-serif; font-size: 10pt; color: #222; }
h1 { text-align: center; letter-spacing: 0.5em; font-size: 20pt; margin: 0 0 8mm; }
.head { display: flex; justify-content: space-between; }
.to { font-size: 13pt; border-bottom: 1px solid #222; padding-bottom: 1mm; width: 85mm; }
.meta td { padding: 0.5mm 2mm; }
.from { margin-top: 4mm; text-align: right; line-height: 1.6; }
table.lines { width: 100%; border-collapse: collapse; margin-top: 6mm; }
table.lines th, table.lines td { border: 1px solid #555; padding: 1.5mm 2mm; }
table.lines th { background: #eee; font-weight: normal; }
td.n { text-align: right; }
.totals { margin-left: auto; margin-top: 3mm; border-collapse: collapse; }
.totals td { border: 1px solid #555; padding: 1.5mm 3mm; }
.note { margin-top: 6mm; font-size: 9pt; }
"""


def page(body: str) -> str:
    return f"<html lang='ja'><head><meta charset='utf-8'><style>{STYLE}</style></head><body>{body}</body></html>"


SAMPLE_1 = page("""
<h1>注文書</h1>
<div class="head">
  <div>
    <p class="to">株式会社サンプル商事 御中</p>
    <p>下記のとおり注文いたします。</p>
    <p>件名: 事務所用品の追加発注</p>
  </div>
  <table class="meta">
    <tr><td>注文番号</td><td>SN-2026-0412</td></tr>
    <tr><td>注文日</td><td>令和8年11月20日</td></tr>
    <tr><td>納期</td><td>2026年12月10日</td></tr>
  </table>
</div>
<div class="from">
  (株)瀬戸内物産 総務部<br>広島県呉市3-3-3<br>TEL 0823-00-0000
</div>
<table class="lines">
  <tr><th>No.</th><th>品番</th><th>品名</th><th>数量</th><th>単位</th><th>単価</th><th>金額</th></tr>
  <tr><td>1</td><td>PPR-A4</td><td>A4コピー用紙(500枚入)</td><td class="n">20</td><td>箱</td><td class="n">600</td><td class="n">12,000</td></tr>
  <tr><td>2</td><td>PEN-BK</td><td>ボールペン 黒</td><td class="n">100</td><td>本</td><td class="n">80</td><td class="n">8,000</td></tr>
  <tr><td>3</td><td>TEA-500</td><td>会議用お茶 500ml ※</td><td class="n">48</td><td>本</td><td class="n">120</td><td class="n">5,760</td></tr>
</table>
<table class="totals">
  <tr><td>小計(税抜)</td><td class="n">25,760</td></tr>
  <tr><td>消費税(10%対象 20,000)</td><td class="n">2,000</td></tr>
  <tr><td>消費税(8%対象 5,760)</td><td class="n">460</td></tr>
  <tr><td>合計</td><td class="n">28,220</td></tr>
</table>
<p class="note">※印は軽減税率(8%)対象です。単価は税抜きです。</p>
""")

SAMPLE_2 = page("""
<h1>発 注 書</h1>
<div class="head">
  <div>
    <p class="to">株式会社サンプル商事 御中</p>
    <p>件名: 年末キャンペーン用 印刷物</p>
  </div>
  <table class="meta">
    <tr><td>発注No.</td><td>MK-1123</td></tr>
    <tr><td>発行日</td><td>2026/11/18</td></tr>
    <tr><td>納入期日</td><td>12月上旬(別途ご相談)</td></tr>
  </table>
</div>
<div class="from">
  宮島観光サービス株式会社<br>広島県廿日市市4-4-4<br>担当: 佐藤
</div>
<table class="lines">
  <tr><th>品名</th><th>数量</th><th>単価(税抜)</th><th>金額</th></tr>
  <tr><td>観光パンフレット印刷(A4 三つ折り)</td><td class="n">3,000部</td><td class="n">18.5</td><td class="n">55,500</td></tr>
  <tr><td>デザイン修正費</td><td class="n">1式</td><td class="n">15,000</td><td class="n">15,000</td></tr>
  <tr><td>ポスター印刷(B2)</td><td class="n">30枚</td><td class="n">450</td><td class="n">13,500</td></tr>
</table>
<table class="totals">
  <tr><td>小計</td><td class="n">84,000</td></tr>
  <tr><td>消費税(10%)</td><td class="n">8,400</td></tr>
  <tr><td>合計金額</td><td class="n">92,400</td></tr>
</table>
<p class="note">備考: 納品は2回に分けても構いません。</p>
""")

SAMPLES = {"注文書_見本1_瀬戸内物産.pdf": SAMPLE_1, "注文書_見本2_宮島観光サービス.pdf": SAMPLE_2}


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, html in SAMPLES.items():
        HTML(string=html).write_pdf(out / name)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "samples"))
