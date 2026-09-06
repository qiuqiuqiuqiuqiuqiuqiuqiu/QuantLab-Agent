"""Refresh all standalone QuantLab explainer pages with the current runtime scope."""
from pathlib import Path

ROOT = Path(__file__).parent
START, END = "<!-- LATEST_RUNTIME_UPDATE:START -->", "<!-- LATEST_RUNTIME_UPDATE:END -->"
STYLE = '''<style>
.latest-runtime-update{margin:42px 0 24px;padding:24px 26px;border:1px solid #9bd8ad;border-radius:14px;background:linear-gradient(135deg,#f1fbf4,#f8fcf8);color:#193324}.latest-runtime-update h2{margin:0 0 10px;color:#123f25}.latest-runtime-update p,.latest-runtime-update li{line-height:1.75}.latest-runtime-update code{background:#e0f3e5;padding:2px 5px;border-radius:4px}.latest-runtime-update .release{font:700 11px ui-monospace,monospace;letter-spacing:.08em;color:#187042}.latest-runtime-update .flow{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0}.latest-runtime-update .flow span{padding:7px 9px;border-radius:7px;background:white;border:1px solid #cce6d2;font-size:13px}.latest-runtime-update .warn{padding:10px 12px;background:#fff7e8;border-left:3px solid #cf932c;border-radius:5px}</style>'''

COMMON = '''<section class="latest-runtime-update"><h2>最新运行时能力：真实数据、受控策略与预测情景</h2><p class="release">CODE UPDATE · 2026-08-27</p><p>最新代码增加 <code>fetch_us_market_data</code>、<code>create_strategy_template</code> 与 <code>forecast_market_direction</code>。用户可输入 <code>英伟达</code>、<code>NVIDIA</code> 或 <code>NVDA</code>；LLM 决定下一步，但固定工具负责名称解析、市场数据快照、数据校验、策略编译、回测和预测诊断。</p><div class="flow"><span>自然语言任务</span><span>→ 行情快照</span><span>→ 数据校验</span><span>→ 策略 / 预测</span><span>→ 受限计算</span><span>→ 证据/治理</span></div><ul><li><code>tools/market_data.py</code>：经固定 Alpha Vantage 适配器获取 OHLCV，校验日期、必填字段、重复日期、价格高低关系和成交量；保存 CSV、lineage JSON、SHA-256。Key 不进入 LLM 上下文。</li><li><code>tools/strategy_template.py</code>：只允许 <code>buy_and_hold</code>、<code>moving_average</code>、<code>mean_reversion</code> 三种策略；校验窗口、z-score、成本和 CSV 列后，生成可审阅的策略规格与标准库脚本。</li><li><code>tools/forecast.py</code>：使用可解释的 AR(1) 历史收益率情景模型，输出上涨概率、统计区间，以及滚动样本外方向准确率和 Brier score；预测 Artifact 含数据 SHA-256 与限制说明。</li><li>策略脚本保存到 <code>backtests/generated/</code>，规则保存到 <code>.quant-agent/strategy-specs/</code>，回测和预测结果保存到 <code>artifacts/</code>；<code>run_backtest</code> 仍负责无 Shell 执行、超时、JSON Schema 与 SHA-256 校验。</li></ul><div class="warn">“分析近三年走势”不等于策略回测或预测。“上涨概率”只是历史模型产生的统计情景，不是必然价格、买卖建议或自动交易依据。复权、幸存者偏差、流动性和样本外验证仍需额外研究。</div></section>'''

TOOL_EXTRA = '''<section class="latest-runtime-update"><h2>本页专项更新：新增三层输入到结果校验</h2><p class="release">TOOL VALIDATION UPDATE · 2026-08-27</p><ul><li><code>fetch_us_market_data</code>：失败时不写入数据快照。</li><li><code>create_strategy_template</code>：CSV 缺 <code>date</code>/<code>close</code>、策略不在白名单或参数越界时，不生成脚本。</li><li><code>run_backtest</code>：仅在生成新 JSON Artifact 且指标、曲线均通过校验时返回 success。</li></ul></section>'''

for path in sorted(ROOT.glob("quantlab-*.html")):
    text = path.read_text(encoding="utf-8")
    replacement = START + STYLE + COMMON + (TOOL_EXTRA if path.name == "quantlab-tool-validation.html" else "") + END
    if START in text:
        before, tail = text.split(START, 1)
        _, after = tail.split(END, 1)
        text = before + after
    # Keep the update above the existing document footer. Appending it after a
    # footer looked like a broken second page in local file:// previews.
    footer_at = text.find('<p class="footer">')
    if footer_at < 0:
        footer_at = text.find('<p class="foot">')
    if footer_at >= 0:
        text = text[:footer_at] + replacement + text[footer_at:]
    else:
        text = text.replace("</main>", replacement + "</main>", 1)
    path.write_text(text, encoding="utf-8")
print(f"Updated {len(list(ROOT.glob('quantlab-*.html')))} HTML pages.")
