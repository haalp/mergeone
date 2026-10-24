import type { Metadata } from "next";

import { GraphView } from "@/components/graph/graph-view";

export const metadata: Metadata = { title: "Graph · mergeone portal" };

export default function GraphPage() {
  return <GraphView />;
}
