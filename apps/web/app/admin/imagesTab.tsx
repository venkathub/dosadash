"use client";

import { useCallback, useState } from "react";
import { Badge, Btn, EmptyState, statusBadgeTone } from "../components/ui";
import { AdminApiError, adminApi, type AdminItem } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

type ImageDraft = {
  item_id: number;
  filename: string;
  status: string;
  model: string;
  prompt_version: string;
  reviewed_by: number | null;
  url: string;
};

/** AI menu photos (Phase 7): the image model drafts, the owner approves —
 * approval publishes the photo with a customer-visible AI label; rejection
 * deletes the file. Nothing reaches the menu without a human decision. */
export function ImagesTab() {
  const load = useCallback(
    () =>
      Promise.all([
        adminApi<AdminItem[]>("/admin/menu/items"),
        adminApi<ImageDraft[]>("/admin/menu-images"),
      ]).then(([items, drafts]) => ({
        items,
        byItem: new Map(drafts.map((d) => [d.item_id, d])),
      })),
    [],
  );
  const { data, error, refresh, setError } = useLoad(load);
  const [busy, setBusy] = useState<number | null>(null);

  const act = (itemId: number, fn: () => Promise<unknown>) => {
    setBusy(itemId);
    return fn()
      .then(refresh)
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "action failed"))
      .finally(() => setBusy(null));
  };

  const generate = (itemId: number) =>
    act(itemId, () => adminApi(`/admin/menu-images/${itemId}/generate`, { method: "POST" }));
  const setStatus = (itemId: number, status: "APPROVED" | "REJECTED") =>
    act(itemId, () =>
      adminApi(`/admin/menu-images/${itemId}/status`, { method: "POST", body: { status } }),
    );

  const items = data?.items ?? [];

  return (
    <div>
      <ErrorBar msg={error} />
      <p className="mb-3 text-xs text-indigo-200/70">
        🎨 The image model drafts a photo from the dish facts — nothing is published without your
        approval, and every approved photo carries a customer-visible ✨ AI label.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((item) => {
          const d = data?.byItem.get(item.id);
          return (
            <div key={item.id} className="rounded-lg bg-indigo-800 p-3 text-sm">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="font-semibold">{item.name}</span>
                {d && <Badge tone={statusBadgeTone(d.status)}>{d.status}</Badge>}
              </div>
              {d && d.status !== "REJECTED" ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={d.url} alt={item.name} className="mb-2 h-40 w-full rounded-lg object-cover" />
              ) : (
                <div className="mb-2 flex h-40 w-full items-center justify-center rounded-lg bg-indigo-950/60 text-xs text-indigo-300">
                  {d?.status === "REJECTED" ? "rejected — file deleted" : "no AI photo yet"}
                </div>
              )}
              {d && <p className="mb-2"><span className="ai-meta">🤖 {d.model} · {d.prompt_version}</span></p>}
              <div className="flex gap-2">
                <Btn variant="ghost" size="sm" disabled={busy !== null} onClick={() => generate(item.id)}>
                  {busy === item.id ? "🎨 Generating…" : d ? "🎨 re-generate" : "🎨 generate"}
                </Btn>
                {d?.status === "DRAFT" && (
                  <>
                    <Btn variant="gold" size="sm" disabled={busy !== null} onClick={() => setStatus(item.id, "APPROVED")}>
                      ✓ Publish (AI-labeled)
                    </Btn>
                    <Btn variant="danger" size="sm" disabled={busy !== null} onClick={() => setStatus(item.id, "REJECTED")}>
                      ✗ Reject
                    </Btn>
                  </>
                )}
                {d?.status === "APPROVED" && (
                  <Btn variant="danger" size="sm" disabled={busy !== null} onClick={() => setStatus(item.id, "REJECTED")}>
                    ✗ Unpublish
                  </Btn>
                )}
              </div>
            </div>
          );
        })}
        {items.length === 0 && <EmptyState>No menu items.</EmptyState>}
      </div>
    </div>
  );
}
