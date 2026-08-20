"use client";

import { useCallback, useState } from "react";
import { AdminApiError, adminApi, type AdminItem } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

const inputCls =
  "rounded border border-stone-600 bg-stone-900 px-2 py-1 text-sm text-stone-100 placeholder-stone-500";
const btnCls = "rounded bg-amber-500 px-3 py-1 text-sm font-semibold text-stone-900 disabled:opacity-40";
const smallBtn = "rounded bg-stone-700 px-2 py-0.5 text-xs disabled:opacity-40";

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

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "APPROVED"
      ? "bg-green-800 text-green-200"
      : status === "REJECTED"
        ? "bg-red-900 text-red-200"
        : "bg-amber-900 text-amber-200";
  return <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>{status}</span>;
}

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
  const [busy, setBusy] = useState<number | "all" | null>(null);
  const [edits, setEdits] = useState<Record<number, { name: string; description: string }>>({});

  const act = (key: number | "all", fn: () => Promise<unknown>) => {
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

  const items = data?.items ?? [];
  const missing = items.filter((i) => !data?.byItem.has(i.id)).length;

  return (
    <div>
      <ErrorBar msg={error} />
      <div className="mb-3 flex items-center gap-3">
        <button className={btnCls} disabled={busy !== null || missing === 0} onClick={draftMissing}>
          {busy === "all" ? "✨ Translating…" : `✨ Draft ${missing} missing (Tamil)`}
        </button>
        <span className="text-xs text-stone-400">
          LLM drafts Tamil text — nothing is served without your approval. Prices and allergens
          always come from the English row.
        </span>
      </div>
      <div className="space-y-2">
        {items.map((item) => {
          const t = data?.byItem.get(item.id);
          const edit = edits[item.id];
          return (
            <div key={item.id} className="flex flex-wrap items-center gap-3 rounded bg-stone-800 p-3 text-sm">
              <span className="w-44 shrink-0 font-semibold">{item.name}</span>
              {t ? (
                <>
                  <input
                    className={`${inputCls} w-56`}
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
                  <input
                    className={`${inputCls} w-72 flex-1`}
                    placeholder="Tamil description"
                    value={edit ? edit.description : (t.description ?? "")}
                    onChange={(e) =>
                      setEdits({
                        ...edits,
                        [item.id]: { name: edit ? edit.name : t.name, description: e.target.value },
                      })
                    }
                  />
                  {t.category_label && <span className="text-xs text-stone-500">{t.category_label}</span>}
                  <StatusBadge status={t.status} />
                  <span className="flex gap-2">
                    {edit && (
                      <button className={smallBtn} disabled={busy !== null} onClick={() => save(item.id)}>
                        💾 Save
                      </button>
                    )}
                    {t.status === "DRAFT" && !edit && (
                      <>
                        <button
                          className="rounded bg-green-800 px-2 py-0.5 text-xs text-green-200 disabled:opacity-40"
                          disabled={busy !== null}
                          onClick={() => setStatus(item.id, "APPROVED")}
                        >
                          ✓ Approve
                        </button>
                        <button
                          className="rounded bg-red-900 px-2 py-0.5 text-xs text-red-200 disabled:opacity-40"
                          disabled={busy !== null}
                          onClick={() => setStatus(item.id, "REJECTED")}
                        >
                          ✗ Reject
                        </button>
                      </>
                    )}
                    <button className={smallBtn} disabled={busy !== null} onClick={() => draftOne(item.id)}>
                      {busy === item.id ? "…" : "✨ re-draft"}
                    </button>
                  </span>
                </>
              ) : (
                <>
                  <span className="text-xs text-stone-500">no Tamil text yet</span>
                  <button className={smallBtn} disabled={busy !== null} onClick={() => draftOne(item.id)}>
                    {busy === item.id ? "…" : "✨ draft"}
                  </button>
                </>
              )}
            </div>
          );
        })}
        {items.length === 0 && <p className="text-sm text-stone-500">No menu items.</p>}
      </div>
    </div>
  );
}
