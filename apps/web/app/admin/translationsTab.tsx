"use client";

import { useCallback, useState } from "react";
import { Badge, Btn, EmptyState, Input, statusBadgeTone } from "../components/ui";
import { AdminApiError, adminApi, type AdminItem } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

export type Translation = {
  item_id: number;
  lang: string;
  name: string;
  description: string | null;
  category_label: string | null;
  status: string;
  model: string;
  prompt_version: string;
  reviewed_by: number | null;
};

const LANG = "ta"; // Tamil-first; more languages join this const later

/** Menu localization (Phase 7, Tamil-first): the LLM drafts Tamil names /
 * descriptions, the owner edits + approves — nothing is served to customers
 * without approval. Prices/allergens/flags stay on the canonical row. */
export function TranslationsTab() {
  const load = useCallback(
    () =>
      Promise.all([
        adminApi<AdminItem[]>("/admin/menu/items"),
        adminApi<Translation[]>(`/admin/translations?lang=${LANG}`),
      ]).then(([items, translations]) => ({
        items,
        byItem: new Map(translations.map((t) => [t.item_id, t])),
      })),
    [],
  );
  const { data, error, refresh, setError } = useLoad(load);
  const [busy, setBusy] = useState<number | "all" | "bulk" | null>(null);
  const [edits, setEdits] = useState<Record<number, { name: string; description: string }>>({});

  const act = (key: number | "all" | "bulk", fn: () => Promise<unknown>) => {
    setBusy(key);
    return fn()
      .then(refresh)
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "action failed"))
      .finally(() => setBusy(null));
  };

  const draftMissing = () =>
    act("all", async () => {
      const r = await adminApi<{ drafted: unknown[]; failed: { item_id: number; error: string }[] }>(
        "/admin/translations/draft",
        { method: "POST", body: { lang: LANG } },
      );
      if (r.failed.length && !r.drafted.length)
        throw new AdminApiError(502, r.failed[0].error);
    });

  const draftOne = (itemId: number) =>
    act(itemId, async () => {
      const r = await adminApi<{ drafted: unknown[]; failed: { item_id: number; error: string }[] }>(
        "/admin/translations/draft",
        { method: "POST", body: { lang: LANG, item_ids: [itemId] } },
      );
      if (r.failed.length) throw new AdminApiError(502, r.failed[0].error);
    });

  const save = (itemId: number) => {
    const edit = edits[itemId];
    return act(itemId, () =>
      adminApi(`/admin/translations/${itemId}/${LANG}`, {
        method: "PATCH",
        body: { name: edit.name, description: edit.description || null },
      }),
    ).then(() => setEdits(({ [itemId]: _gone, ...rest }) => rest));
  };

  const setStatus = (itemId: number, status: "APPROVED" | "REJECTED") =>
    act(itemId, () =>
      adminApi(`/admin/translations/${itemId}/${LANG}/status`, { method: "POST", body: { status } }),
    );

  const bulkApproveAll = () =>
    act("bulk", () =>
      adminApi<{ changed: number; skipped: number }>("/admin/translations/bulk-status", {
        method: "POST",
        body: { lang: LANG, status: "APPROVED" },
      }),
    );

  const items = data?.items ?? [];
  const missing = items.filter((i) => !data?.byItem.has(i.id)).length;
  const draftCount = Array.from(data?.byItem.values() ?? []).filter(
    (t) => t.status === "DRAFT",
  ).length;

  return (
    <div>
      <ErrorBar msg={error} />
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Btn variant="turmeric" size="sm" disabled={busy !== null || missing === 0} onClick={draftMissing}>
          {busy === "all" ? "✨ Translating…" : `✨ Draft ${missing} missing (Tamil)`}
        </Btn>
        {draftCount > 0 && (
          <Btn variant="indigo" size="sm" disabled={busy !== null} onClick={bulkApproveAll}>
            {busy === "bulk" ? "✓ Approving…" : `✓ Approve all ${draftCount} DRAFT`}
          </Btn>
        )}
        <span className="text-xs text-indigo-200/70">
          LLM drafts Tamil text — nothing is served without your approval. Prices and allergens
          always come from the English row.
        </span>
      </div>
      <div className="space-y-2">
        {items.map((item) => {
          const t = data?.byItem.get(item.id);
          const edit = edits[item.id];
          return (
            <div key={item.id} className="flex flex-wrap items-center gap-3 rounded-lg bg-indigo-800 p-3 text-sm">
              <span className="w-44 shrink-0 font-semibold">{item.name}</span>
              {t ? (
                <>
                  <Input
                    tone="dark"
                    className="w-56 px-2 py-1"
                    value={edit ? edit.name : t.name}
                    onChange={(e) =>
                      setEdits({
                        ...edits,
                        [item.id]: {
                          name: e.target.value,
                          description: edit ? edit.description : (t.description ?? ""),
                        },
                      })
                    }
                  />
                  <Input
                    tone="dark"
                    className="w-72 flex-1 px-2 py-1"
                    placeholder="Tamil description"
                    value={edit ? edit.description : (t.description ?? "")}
                    onChange={(e) =>
                      setEdits({
                        ...edits,
                        [item.id]: { name: edit ? edit.name : t.name, description: e.target.value },
                      })
                    }
                  />
                  {t.category_label && <span className="text-xs text-indigo-200/60">{t.category_label}</span>}
                  <Badge tone={statusBadgeTone(t.status)}>{t.status}</Badge>
                  <span className="ai-meta">🤖 {t.model} · {t.prompt_version}</span>
                  <span className="flex gap-2">
                    {edit && (
                      <Btn variant="indigo" size="sm" disabled={busy !== null} onClick={() => save(item.id)}>
                        💾 Save
                      </Btn>
                    )}
                    {t.status === "DRAFT" && !edit && (
                      <>
                        <Btn variant="turmeric" size="sm" disabled={busy !== null} onClick={() => setStatus(item.id, "APPROVED")}>
                          ✓ Approve
                        </Btn>
                        <Btn variant="danger" size="sm" disabled={busy !== null} onClick={() => setStatus(item.id, "REJECTED")}>
                          ✗ Reject
                        </Btn>
                      </>
                    )}
                    <Btn variant="ghost" size="sm" disabled={busy !== null} onClick={() => draftOne(item.id)}>
                      {busy === item.id ? "…" : "✨ re-draft"}
                    </Btn>
                  </span>
                </>
              ) : (
                <>
                  <span className="text-xs text-indigo-200/60">no Tamil text yet</span>
                  <Btn variant="ghost" size="sm" disabled={busy !== null} onClick={() => draftOne(item.id)}>
                    {busy === item.id ? "…" : "✨ draft"}
                  </Btn>
                </>
              )}
            </div>
          );
        })}
        {items.length === 0 && <EmptyState>No menu items.</EmptyState>}
      </div>
    </div>
  );
}
