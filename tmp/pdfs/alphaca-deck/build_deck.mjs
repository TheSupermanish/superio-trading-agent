import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const workspaceDir = "/Users/beyond/Desktop/projects/superio";
const SKILL_DIR = "/Users/beyond/.codex/plugins/cache/openai-primary-runtime/presentations/26.903.11726/skills/presentations";
const TMP_DIR = path.join(workspaceDir, "tmp/pdfs/alphaca-deck");
const FINAL_PPTX = path.join(TMP_DIR, "alphaca-hackathon-pitch-final-v4.pptx");
const dashboardPath = path.join(TMP_DIR, "assets/dashboard.png");
const mascotPath = path.join(workspaceDir, "dashboard/public/alphaca_icon.jpg");

const { resolvePresentationFont, applyPresentationChartFont, finalizePresentation } = await import(
  pathToFileURL(path.join(SKILL_DIR, "container_tools/artifact_tool_utils.mjs")).href,
);

const family = resolvePresentationFont();
const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });

const C = {
  bg: "#090B10",
  panel: "#10151F",
  panel2: "#151C28",
  grid: "#202938",
  text: "#EDF2F7",
  muted: "#91A0B5",
  amber: "#FFBF18",
  green: "#16D9A0",
  red: "#FF6174",
  blue: "#56A7FF",
  white: "#FFFFFF",
};

function box(slide, left, top, width, height, fill = C.panel, radius = "roundRect", line = C.grid) {
  return slide.shapes.add({
    geometry: radius,
    position: { left, top, width, height },
    fill,
    line: { fill: line, width: 1 },
  });
}

function rule(slide, left, top, width, height = 2, fill = C.amber) {
  return slide.shapes.add({
    geometry: "rect",
    position: { left, top, width, height },
    fill,
    line: { fill: "none", width: 0 },
  });
}

function text(slide, value, left, top, width, height, opts = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position: { left, top, width, height },
    fill: "none",
    line: { fill: "none", width: 0 },
  });
  shape.text = value;
  shape.text.style = {
    typeface: family,
    fontSize: opts.size ?? 24,
    bold: opts.bold ?? false,
    color: opts.color ?? C.text,
    autoFit: opts.autoFit ?? "shrinkText",
    horizontalAlignment: opts.align ?? "left",
    verticalAlignment: opts.valign ?? "middle",
    marginLeft: opts.margin ?? 0,
    marginRight: opts.margin ?? 0,
    marginTop: opts.margin ?? 0,
    marginBottom: opts.margin ?? 0,
  };
  return shape;
}

function baseSlide(section, number) {
  const slide = presentation.slides.add();
  slide.background.fill = C.bg;
  rule(slide, 0, 0, 1280, 5, C.amber);
  text(slide, section.toUpperCase(), 66, 32, 900, 24, { size: 12, bold: true, color: C.amber });
  text(slide, String(number).padStart(2, "0"), 1190, 670, 40, 22, { size: 11, color: C.muted, align: "right" });
  return slide;
}

function title(slide, value, subtitle = "") {
  text(slide, value, 66, 72, 1148, 62, { size: 37, bold: true });
  if (subtitle) text(slide, subtitle, 66, 135, 1110, 48, { size: 18, color: C.muted });
}

function label(slide, value, left, top, width, color = C.muted) {
  text(slide, value.toUpperCase(), left, top, width, 22, { size: 11, bold: true, color });
}

function metric(slide, left, top, width, metricLabel, value, note, color = C.text) {
  label(slide, metricLabel, left, top, width);
  text(slide, value, left, top + 24, width, 52, { size: 34, bold: true, color });
  if (note) text(slide, note, left, top + 75, width, 42, { size: 14, color: C.muted });
}

function notes(slide, value) {
  slide.speakerNotes.textFrame.setText(value);
}

// 1. Cover
{
  const slide = presentation.slides.add();
  slide.background.fill = C.bg;
  rule(slide, 0, 0, 1280, 7, C.amber);
  rule(slide, 66, 194, 96, 5, C.amber);
  text(slide, "ALPACA AI TRADING AGENTS HACKATHON", 66, 60, 690, 28, { size: 14, bold: true, color: C.muted });
  text(slide, "alphaca", 66, 102, 310, 92, { size: 64, bold: true, color: C.text });
  text(slide, "Autonomous options trading with deterministic risk control", 66, 228, 790, 92, { size: 31, bold: true });
  text(slide, "Gemini reads the market and selects an approved structure. Code controls every leg, size, limit, and exit.", 66, 332, 760, 92, { size: 20, color: C.muted });
  const mascot = await fs.readFile(mascotPath);
  slide.images.add({
    blob: mascot,
    contentType: "image/jpeg",
    alt: "Alphaca mascot wearing sunglasses",
    fit: "cover",
    geometry: "ellipse",
    position: { left: 915, top: 118, width: 250, height: 250 },
  });
  rule(slide, 915, 390, 250, 3, C.green);
  text(slide, "PAPER TRADING", 915, 406, 250, 24, { size: 13, bold: true, color: C.green, align: "center" });
  text(slide, "Nine ETFs across three independently managed paper accounts", 66, 622, 980, 30, { size: 16, color: C.muted });
  text(slide, "SEP 2026", 1110, 622, 90, 30, { size: 12, color: C.muted, align: "right" });
  notes(slide, "Project sources: README.md, engine/config.py, and dashboard/public/snapshot*.json. This project uses Alpaca paper accounts only.");
}

// 2. Problem
{
  const slide = baseSlide("Problem", 2);
  title(slide, "Models can read markets. Limits need code.");
  text(slide, "A useful trading agent must reason under uncertainty without gaining authority over the account's safety boundaries.", 66, 158, 1100, 54, { size: 21, color: C.muted });
  box(slide, 66, 245, 530, 294, C.panel);
  label(slide, "AI is good at", 96, 276, 440, C.green);
  text(slide, "Reading regime and volatility", 96, 315, 430, 36, { size: 22, bold: true });
  text(slide, "Interpreting news and catalysts", 96, 372, 430, 36, { size: 22, bold: true });
  text(slide, "Comparing strategy tradeoffs", 96, 429, 430, 36, { size: 22, bold: true });
  box(slide, 626, 245, 530, 294, C.panel);
  label(slide, "Code must own", 656, 276, 440, C.amber);
  text(slide, "Position sizing and maximum loss", 656, 315, 430, 36, { size: 22, bold: true });
  text(slide, "Liquidity and event constraints", 656, 372, 430, 36, { size: 22, bold: true });
  text(slide, "Orders, exits, and kill switches", 656, 429, 430, 36, { size: 22, bold: true });
  text(slide, "Alphaca separates judgment from authority.", 66, 582, 1090, 46, { size: 27, bold: true, color: C.amber });
  notes(slide, "Design rationale and system thesis are documented in README.md and docs/WRITEUP.md.");
}

// 3. Architecture
{
  const slide = baseSlide("Architecture", 3);
  title(slide, "Six roles turn market data into a managed position");
  const steps = [
    ["01", "Scout", "Measures trend, realized volatility, implied volatility, and their spread", C.blue],
    ["02", "Analyst", "Uses Gemini with search grounding to capture news and scheduled catalysts", C.green],
    ["03", "Strategist", "Compares approved structures and returns a proposal reference", C.amber],
    ["04", "Risk officer", "Applies deterministic gates and computes a safe quantity", C.red],
    ["05", "Executor", "Places reproducible multi-leg orders through the Alpaca CLI", C.blue],
    ["06", "Manager", "Monitors fills, profit targets, stops, and expiration safety", C.green],
  ];
  for (let i = 0; i < steps.length; i++) {
    const [n, name, desc, color] = steps[i];
    const top = 170 + i * 74;
    text(slide, n, 72, top, 44, 42, { size: 18, bold: true, color });
    rule(slide, 124, top + 20, 66, 2, color);
    text(slide, name, 212, top - 2, 205, 44, { size: 23, bold: true });
    text(slide, desc, 430, top - 2, 720, 48, { size: 17, color: C.muted });
  }
  notes(slide, "Architecture source: README.md. Model routing source: engine/llm.py. Execution and management source: engine/executor.py and engine/manager.py.");
}

// 4. Safe action space
{
  const slide = baseSlide("Control model", 4);
  title(slide, "The strategist chooses from trades that already passed risk review");
  text(slide, "The model never emits a strike, quantity, price, or option leg.", 66, 150, 1090, 42, { size: 23, bold: true, color: C.amber });
  box(slide, 66, 234, 336, 245, C.panel);
  label(slide, "1. Candidate factory", 94, 258, 280, C.blue);
  text(slide, "Deterministic code builds defined-risk structures from the live chain.", 94, 300, 278, 118, { size: 22, bold: true });
  rule(slide, 402, 354, 72, 3, C.blue);
  box(slide, 474, 234, 336, 245, C.panel);
  label(slide, "2. Risk verdict", 502, 258, 280, C.red);
  text(slide, "Each proposal receives a named approval or rejection before the model sees it.", 502, 300, 278, 118, { size: 22, bold: true });
  rule(slide, 810, 354, 72, 3, C.red);
  box(slide, 882, 234, 306, 245, C.panel);
  label(slide, "3. Selection", 910, 258, 250, C.green);
  text(slide, "Gemini returns an approved proposal ID or declines to trade.", 910, 300, 248, 118, { size: 22, bold: true });
  box(slide, 192, 538, 900, 80, C.panel2);
  text(slide, "A confused model can choose a worse safe trade. It cannot create an unsafe one.", 226, 552, 832, 52, { size: 22, bold: true, color: C.green, align: "center" });
  notes(slide, "The strategist's tool loop and bounded output are described in README.md and docs/WRITEUP.md. Candidate construction and gate evaluation live in engine/structures.py and engine/risk.py.");
}

// 5. Risk gates
{
  const slide = baseSlide("Risk system", 5);
  title(slide, "Eight checks stand between an idea and an order", "Current barbell preset. Limits are explicit, tested, and journalled by name.");
  const gates = [
    ["G1", "Kill switches", "4% day loss, 10% total drawdown"],
    ["G2", "Trade budget", "7 new trades, 10 open structures"],
    ["G3", "Defined risk", "Every short leg requires matching cover"],
    ["G4", "Liquidity", "Rejects wide or unusable quotes"],
    ["G5", "Pricing", "18% credit floor, 45% debit cap"],
    ["G6", "Event blackout", "Blocks premium selling across catalysts"],
    ["G7", "Position sizing", "1% tactical, 1.25% carry per trade"],
    ["G8", "Portfolio limits", "8% open risk with sleeve and symbol caps"],
  ];
  for (let i = 0; i < gates.length; i++) {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const left = 66 + col * 590;
    const top = 205 + row * 92;
    box(slide, left, top, 558, 72, C.panel);
    text(slide, gates[i][0], left + 18, top + 13, 54, 42, { size: 16, bold: true, color: C.amber, align: "center" });
    text(slide, gates[i][1], left + 84, top + 8, 178, 30, { size: 18, bold: true });
    text(slide, gates[i][2], left + 84, top + 36, 440, 25, { size: 14, color: C.muted });
  }
  text(slide, "Naked short options are structurally impossible. No configuration flag disables the coverage rule.", 66, 602, 1120, 34, { size: 19, bold: true, color: C.green });
  notes(slide, "Current limits verified from engine/config.py on 4 September 2026. Gate implementation source: engine/risk.py. All values refer to Alpaca paper trading.");
}

// 6. Strategy portfolio
{
  const slide = baseSlide("Strategy experiment", 6);
  title(slide, "Three accounts test different sources of return on the same tape");
  const cols = [
    [66, "BARBELL", "Balanced", "Core income, convex trades, and financed carry", "Best for changing volatility regimes", C.amber],
    [458, "CONVEX TILT", "Asymmetric", "More budget for debit spreads and long gamma", "Benefits from large directional moves", C.green],
    [850, "INCOME ONLY", "Control", "Defined-risk premium selling without convexity", "Tests whether volatility risk premium alone works", C.blue],
  ];
  for (const [left, tag, name, desc, note, color] of cols) {
    box(slide, left, 190, 352, 354, C.panel);
    label(slide, tag, left + 28, 218, 296, color);
    text(slide, name, left + 28, 252, 296, 50, { size: 30, bold: true });
    rule(slide, left + 28, 316, 70, 3, color);
    text(slide, desc, left + 28, 346, 292, 92, { size: 20, bold: true });
    text(slide, note, left + 28, 456, 292, 60, { size: 15, color: C.muted });
  }
  text(slide, "Strategy set: credit spreads, debit spreads, iron condors, and financed risk reversals", 66, 586, 1120, 38, { size: 20, bold: true, color: C.text });
  text(slide, "Universe: SPY, QQQ, IWM, DIA, XLK, XLF, SMH, GLD, and TLT", 66, 624, 1120, 28, { size: 15, color: C.muted });
  notes(slide, "Profiles and strategy variants are documented in README.md. Universe and sleeve configuration source: engine/config.py.");
}

// 7. Results
{
  const slide = baseSlide("Paper results", 7);
  title(slide, "The balanced account leads the live three-account test", "Broker equity return as shown on 4 September 2026. The sample covers fewer than five sessions.");
  const chart = slide.charts.add("bar", {
    position: { left: 68, top: 210, width: 650, height: 360 },
    categories: ["Barbell", "Convex tilt", "Income only"],
    series: [{
      name: "Return",
      values: [0.0145, 0.0079, -0.0064],
      fill: C.amber,
      points: [
        { idx: 0, fill: C.green },
        { idx: 1, fill: C.blue },
        { idx: 2, fill: C.red },
      ],
      valuesFormatCode: "0.00%",
    }],
    barOptions: { direction: "bar", grouping: "clustered", gapWidth: 70 },
    hasLegend: false,
    dataLabels: { showValue: false },
    xAxis: { numberFormatCode: "0.00%", minimumScale: -0.01, maximumScale: 0.02 },
    chartFill: C.bg,
    chartLine: { fill: C.grid, width: 1 },
    plotAreaFill: C.bg,
    plotAreaLine: { fill: C.grid, width: 1 },
  });
  applyPresentationChartFont(chart, { fontFamily: family });
  text(slide, "-0.64%", 206, 265, 140, 34, { size: 18, bold: true, color: C.white, align: "center" });
  text(slide, "0.79%", 418, 379, 140, 34, { size: 18, bold: true, color: C.white, align: "center" });
  text(slide, "1.45%", 580, 494, 140, 34, { size: 18, bold: true, color: C.white, align: "center" });
  box(slide, 768, 210, 420, 385, C.panel);
  metric(slide, 802, 236, 350, "Main account equity", "$101,451.99", "+$1,451.99 from $100,000", C.green);
  metric(slide, 802, 354, 350, "Peak equity", "$101,965.56", "+1.97% intraday peak", C.amber);
  metric(slide, 802, 472, 350, "Maximum drawdown", "0.69%", "Observed on the main account", C.text);
  text(slide, "Paper results are experimental. They do not establish a durable edge.", 68, 606, 1120, 38, { size: 18, bold: true, color: C.red });
  notes(slide, "Main values come from the live dashboard screenshot captured at 10:18 ET on 4 September 2026. Test2 and test3 returns come from dashboard/public/snapshot-test2.json and snapshot-test3.json. Percent figures are displayed as percentage points, not decimal fractions. Paper trading results are not live trading results.");
}

// 8. Dashboard and audit trail
{
  const slide = baseSlide("Auditability", 8);
  title(slide, "Every fill, refusal, and risk decision is visible");
  const screenshot = await fs.readFile(dashboardPath);
  slide.images.add({
    blob: screenshot,
    contentType: "image/png",
    alt: "Alphaca live paper trading dashboard showing account value and performance",
    fit: "cover",
    geometry: "roundRect",
    borderRadius: "rounded-xl",
    position: { left: 66, top: 160, width: 806, height: 454 },
    crop: { left: 0, top: 0, right: 0.08, bottom: 0.08 },
  });
  label(slide, "PUBLIC DASHBOARD", 918, 176, 268, C.green);
  text(slide, "Live broker equity", 918, 216, 270, 34, { size: 21, bold: true });
  text(slide, "Portfolio Greeks and payoff curves", 918, 274, 270, 52, { size: 19, bold: true });
  text(slide, "Open and closed structures", 918, 346, 270, 34, { size: 19, bold: true });
  text(slide, "Named rejection reasons", 918, 404, 270, 34, { size: 19, bold: true });
  text(slide, "Stress tests and session plans", 918, 462, 270, 52, { size: 19, bold: true });
  text(slide, "Public link on the closing slide", 918, 568, 270, 34, { size: 14, color: C.amber });
  notes(slide, "Dashboard screenshot captured from the locally served current build on 4 September 2026. Public mirror: https://thesupermanish.github.io/superio-trading-agent/");
}

// 9. Close
{
  const slide = baseSlide("Conclusion", 9);
  title(slide, "Autonomy without unchecked financial authority");
  text(slide, "Alphaca shows how an AI trading agent can act independently while deterministic software keeps control of account risk.", 66, 150, 1080, 88, { size: 28, bold: true });
  rule(slide, 66, 266, 1100, 2, C.grid);
  metric(slide, 66, 310, 300, "Market coverage", "9 ETFs", "Liquid equity, sector, commodity, and bond proxies", C.blue);
  metric(slide, 426, 310, 300, "Portfolio experiment", "3 accounts", "Balanced, convex tilt, and income control", C.green);
  metric(slide, 786, 310, 360, "Open risk ceiling", "8%", "Defined risk with a 10% drawdown stand-down", C.amber);
  box(slide, 66, 488, 1100, 102, C.panel2);
  text(slide, "Repository", 92, 507, 140, 24, { size: 13, bold: true, color: C.muted });
  text(slide, "github.com/TheSupermanish/superio-trading-agent", 92, 536, 560, 30, { size: 18, bold: true, color: C.text });
  text(slide, "Live demo", 712, 507, 120, 24, { size: 13, bold: true, color: C.muted });
  text(slide, "thesupermanish.github.io/superio-trading-agent", 712, 536, 420, 30, { size: 17, bold: true, color: C.amber });
  text(slide, "Built on Alpaca paper trading with Gemini, Vertex AI, Python, and Next.js", 66, 624, 1090, 28, { size: 15, color: C.muted });
  notes(slide, "Repository: https://github.com/TheSupermanish/superio-trading-agent. Public demo: https://thesupermanish.github.io/superio-trading-agent/. All performance shown in this deck comes from paper accounts over a short hackathon window.");
}

await fs.mkdir(TMP_DIR, { recursive: true });
const stagingDir = path.join(workspaceDir, ".codex-finalizer");
await fs.mkdir(stagingDir, { recursive: true });
const candidatePath = path.join(stagingDir, "alphaca-candidate.pptx");
await (await PresentationFile.exportPptx(presentation)).save(candidatePath);

const requirements = {
  explicitTotalSlideCount: 9,
  requiredNativeTableOwnerSlides: [],
  requiredNativeChartOwnerSlides: [7],
  materializeLiteralChartWorkbooks: true,
};

await finalizePresentation({
  ...requirements,
  workspaceDir,
  candidatePath,
  finalPath: FINAL_PPTX,
  pythonExecutable: process.env.RUNTIME_PYTHON,
  integrityValidatorPath: path.join(SKILL_DIR, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(SKILL_DIR, "container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs: [
    "--expected-slide-size-emu", "12192000,6858000",
    "--validate-heading-fit",
  ],
  requiredNativeTableOwnerSlides: [],
  fontPolicy: { basis: "design", families: [family] },
  verifyArtifactToolImport: true,
  receiptPath: path.join(stagingDir, "alphaca-hackathon-pitch-v4.validation.json"),
});

console.log(FINAL_PPTX);
