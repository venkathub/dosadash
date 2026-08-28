"use client";

import { useCallback, useState } from "react";
import { Badge, Btn, EmptyState, Eyebrow, Input } from "../components/ui";
import { AdminApiError, adminApi } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

type McpKey = {
  id: number;
  name: string;
  key_prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

type McpKeyCreated = McpKey & { key: string };

const fmt = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "—";

function CopyBtn({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Btn
      variant="indigo"
      size="sm"
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? "✓ Copied" : (label ?? "Copy")}
    </Btn>
  );
}

function Snippet({ title, note, text }: { title: string; note: string; text: string }) {
  return (
    <div className="rounded-lg bg-indigo-900 p-3">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-display text-[13px] font-bold text-turmeric-400">{title}</span>
        <CopyBtn text={text} />
      </div>
      <p className="mb-2 text-xs text-indigo-300">{note}</p>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-indigo-950 p-2 text-[11px] text-indigo-100">
        {text}
      </pre>
    </div>
  );
}

/** Per-client setup, rendered ONCE with the fresh plaintext key embedded. */
function ClientSetup({ created, onDismiss }: { created: McpKeyCreated; onDismiss: () => void }) {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const mcpUrl = `${origin}/mcp`;
  const tokenizedUrl = `${origin}/mcp/${created.key}`;
  const cursorConfig = { url: mcpUrl, headers: { Authorization: `Bearer ${created.key}` } };
  const cursorDeeplink = `cursor://anysphere.cursor-deeplink/mcp/install?name=dosadash&config=${btoa(
    JSON.stringify(cursorConfig),
  )}`;
  return (
    <div className="mb-4 rounded-lg border-2 border-turmeric-500 bg-indigo-800 p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-display text-sm font-bold text-white">
          🔑 Key “{created.name}” created — shown ONCE, copy it now
        </span>
        <Btn variant="ghost" size="sm" onClick={onDismiss}>
          Done — hide key
        </Btn>
      </div>
      <div className="mb-3 flex items-center gap-2">
        <code className="break-all rounded bg-indigo-950 px-2 py-1 text-[12px] text-turmeric-300">
          {created.key}
        </code>
        <CopyBtn text={created.key} label="Copy key" />
      </div>
      <div className="grid gap-2 lg:grid-cols-2">
        <Snippet
          title="ChatGPT (connector)"
          note="Settings → Apps & Connectors → Advanced → enable Developer mode, then Create → paste this URL, Authentication: None (the key travels in the URL — ChatGPT has no header field)."
          text={tokenizedUrl}
        />
        <Snippet
          title="Claude Code (CLI & Web)"
          note="One command — or commit-free via the repo's .mcp.json with DOSADASH_MCP_KEY exported."
          text={`claude mcp add --transport http dosadash ${mcpUrl} --header "Authorization: Bearer ${created.key}"`}
        />
        <Snippet
          title="Cursor (one-click)"
          note="Open this deeplink to install, or drop the JSON into .cursor/mcp.json."
          text={cursorDeeplink}
        />
        <Snippet
          title="Cursor / Claude Desktop (JSON)"
          note='Cursor: .cursor/mcp.json → "mcpServers". Claude Desktop: Settings → Connectors → Add custom connector with the /mcp URL, or use this remote config.'
          text={JSON.stringify({ mcpServers: { dosadash: cursorConfig } }, null, 2)}
        />
      </div>
      <p className="mt-2 text-xs text-indigo-300">
        Revoking the key below disconnects every client using it within ~60s.
      </p>
    </div>
  );
}

export function McpTab() {
  const loadKeys = useCallback(() => adminApi<McpKey[]>("/admin/mcp-keys"), []);
  const { data: keys, error, refresh, setError } = useLoad(loadKeys);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<McpKeyCreated | null>(null);
  const [busy, setBusy] = useState(false);

  const createKey = () => {
    if (!name.trim()) return;
    setBusy(true);
    adminApi<McpKeyCreated>("/admin/mcp-keys", { method: "POST", body: { name: name.trim() } })
      .then((k) => {
        setCreated(k);
        setName("");
        setError("");
        refresh();
      })
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "create failed"))
      .finally(() => setBusy(false));
  };

  const revoke = (k: McpKey) => {
    if (!window.confirm(`Revoke “${k.name}” (${k.key_prefix}…)? Clients using it stop working in ~60s.`))
      return;
    adminApi(`/admin/mcp-keys/${k.id}/revoke`, { method: "POST" })
      .then(refresh)
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "revoke failed"));
  };

  return (
    <div>
      <ErrorBar msg={error} />
      <p className="mb-3 text-sm text-indigo-200">
        API keys for the hosted MCP endpoint (<code className="text-turmeric-300">/mcp</code>) — connect
        ChatGPT, Cursor, Claude Code (web &amp; CLI) or Claude Desktop to the live menu, kitchen
        inventory and REAL ordering. Full walkthrough: <code>docs/09-mcp-demo.md</code>.
      </p>

      {created && <ClientSetup created={created} onDismiss={() => setCreated(null)} />}

      <div className="mb-4 flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-indigo-300">
            New key label (which client / whose machine)
          </span>
          <Input
            tone="dark"
            value={name}
            placeholder="e.g. ChatGPT — Venkatesh"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createKey()}
          />
        </label>
        <Btn onClick={createKey} disabled={busy || !name.trim()}>
          {busy ? "Generating…" : "🔑 Generate key"}
        </Btn>
      </div>

      {keys && keys.length === 0 && <EmptyState>No MCP keys yet — generate one above.</EmptyState>}
      <div className="space-y-2">
        {(keys ?? []).map((k) => (
          <div
            key={k.id}
            className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg bg-indigo-800 p-3"
          >
            <span className="font-display text-sm font-bold text-white">{k.name}</span>
            <code className="text-xs text-indigo-300">{k.key_prefix}…</code>
            <span className="text-xs text-indigo-300">created {fmt(k.created_at)}</span>
            <span className="text-xs text-indigo-300">last used {fmt(k.last_used_at)}</span>
            <span className="ml-auto flex items-center gap-2">
              {k.revoked_at ? (
                <Badge tone="danger">revoked</Badge>
              ) : (
                <>
                  <Badge tone="success">active</Badge>
                  <Btn variant="danger" size="sm" onClick={() => revoke(k)}>
                    Revoke
                  </Btn>
                </>
              )}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-4 rounded-lg bg-indigo-900 p-3 text-xs text-indigo-300">
        <Eyebrow>How it works</Eyebrow>
        <p className="mt-1">
          The server exposes three tools — <code>get_menu</code>, <code>check_inventory</code>,{" "}
          <code>place_order</code> — and every order runs through the real order pipeline (item
          validation, GST, kitchen hours) as the “Claude (MCP demo)” customer, appearing live on the
          KDS. Keys are stored hashed; the plaintext is only ever shown at creation.
        </p>
      </div>
    </div>
  );
}
