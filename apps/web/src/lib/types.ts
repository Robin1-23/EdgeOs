// Shapes returned by services/api/main.py (the report is services/agents/mother/judge.Report).

export type Metric = {
  id: string;
  name: string;
  value: number | string;
  unit: string | null;
  formula_id: string;
  source_query_id: string;
  formula: string | null;
};
export type Finding = { id: string; text: string; metric_ids: string[] };
export type Flag = { severity: "info" | "watch" | "alert"; reason: string; metric_ids: string[] };
export type Source = { id: string; kind: string; ref: string; retrieved_at: string; providers: string[] };
export type Gap = { item: string; reason: string };
export type Envelope = {
  agent_id: string;
  subject: string;
  as_of: string;
  metrics: Metric[];
  findings: Finding[];
  flags: Flag[];
  sources: Source[];
  confidence: { level: "high" | "medium" | "low"; reason: string };
  gaps: Gap[];
};
export type AgentSlot = { available: true; envelope: Envelope } | { available: false; reason: string };

export type CitedText = { text: string; cites: string[] };
export type ReportItem = { id: string; agent_id: string; subject: string; text: string; sources: string[] };
export type Report = {
  kind: string;
  subject: string;
  as_of: string;
  status: "published" | "blocked";
  summary: CitedText[];
  key_items: ReportItem[];
  bull: CitedText[];
  bear: CitedText[];
  risks: ReportItem[];
  risk_notes: CitedText[];
  gaps: string[];
  confidence: "high" | "medium" | "low";
  confidence_reason: string;
  disclaimer: string;
};
type Page<M> = { dev_only: boolean; subject: string; as_of: string; report: Report; agents: Record<string, AgentSlot>; meta: M | null };
export type StockPage = Page<{ name: string | null; isin: string; symbol: string | null; sector: string | null; industry: string | null }>;
export type FundPage = Page<{ name: string; amc: string; category: string; plan: string | null; option: string | null }>;
export type SeriesPoint = { date: string; value: number };
export type FundSeries = {
  dev_only: boolean;
  fund: SeriesPoint[];
  benchmark: { name: string; label: string; points: SeriesPoint[] } | null;
  formula: string;
};

export type IndexTile = { name: string; date: string; close: number; change: number | null };
export type Home = {
  dev_only: boolean;
  indices: IndexTile[];
  breadth: { date: string; advances: number; declines: number; unchanged: number; formula: string } | null;
};
export type SearchResult = {
  dev_only: boolean;
  stocks: { symbol: string; name: string | null; isin: string }[];
  funds: { amfi_code: string; name: string }[];
};
export type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number };
