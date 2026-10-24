import type { Metadata } from "next";

import { QueueView } from "@/components/queue/queue-view";

export const metadata: Metadata = { title: "Queue · mergeone portal" };

export default function QueuePage() {
  return <QueueView />;
}
