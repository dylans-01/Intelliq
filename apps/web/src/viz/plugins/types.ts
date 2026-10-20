import type { ComponentType } from "react";
import type { Channel, QueryResult, VizSpec, VizType } from "@/api/types";
import type { RenderContext, Tuple } from "../model";

export interface PickResult {
  tuple: Tuple;
}

export interface BuildResult {
  /** ECharts option, or null when the configuration is incomplete. */
  option: Record<string, unknown> | null;
  message?: string;
  /** Map a clicked mark (series, data index) back to its dimension values. */
  pick?: (seriesIndex: number, dataIndex: number) => PickResult | null;
  /** Map an axis click (category index) back to its dimension values. */
  axisPick?: (dataIndex: number) => PickResult | null;
}

export interface ChannelSpec {
  channel: Channel;
  label: string;
  roles: ("dimension" | "measure")[];
  max?: number;
  required?: boolean;
  hint?: string;
}

export interface ComponentProps {
  ctx: RenderContext;
  result: QueryResult;
  onSelect: (tuple: Tuple | null, additive: boolean) => void;
}

export interface ChartPlugin {
  type: VizType;
  label: string;
  description: string;
  channels: ChannelSpec[];
  /** Either an ECharts builder or a React component renderer. */
  build?: (ctx: RenderContext) => BuildResult;
  component?: ComponentType<ComponentProps>;
  /** Builder hint: is this spec complete enough to query? */
  ready: (viz: VizSpec) => string | null;
}
