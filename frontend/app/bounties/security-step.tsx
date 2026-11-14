"use client";

import { FormEvent, useEffect, useState } from "react";
import { AccessTest, api, errorText, ownerKeyName } from "./api";
import { CopyButton, Message } from "./ui";

const HINTS: Record<string, string> = {
  "other user's data": "Put it in a second test user's profile or order.",
  "data table row": "Put it in a database row of that second user.",
  "admin-only data": "Put it in a page or API answer only admins can see.",
  "chatbot instructions":
    "Put it in your chatbot's hidden instructions (skip if you have no chatbot).",
};

function hint(label: string): string {
  return (
    HINTS[label.toLowerCase()] ??
    "Hide it where the test user must never reach."
  );
}

type Form = {
  path: string;
  userKey: string;
  userVal: string;
  passKey: string;
  passVal: string;
};

const EMPTY: Form = {
  path: "/api/login",
  userKey: "email",
  userVal: "",
  passKey: "password",
  passVal: "",
};

function toForm(t: AccessTest): Form {
  const entries = Object.entries(t.login_body ?? {});
  if (!t.login_path || entries.length < 2) return EMPTY;
  const [[userKey, userVal], [passKey, passVal]] = entries;
  return {
    path: t.login_path,
    userKey,
    userVal: String(userVal),
    passKey,
    passVal: String(passVal),
  };
}

export function SecurityStep({
  bountyId,
  demo,
  ready,
  onChange,
}: {
  bountyId: string;
  demo: boolean;
  ready: boolean;
  onChange: () => Promise<void>;
}) {
  const [key, setKey] = useState<string | null | undefined>(undefined);
  const [test, setTest] = useState<AccessTest | null>(null);
  const [form, setForm] = useState<Form>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setKey(localStorage.getItem(ownerKeyName(bountyId)));
  }, [bountyId]);

  useEffect(() => {
    if (demo || !key) return;
    api<AccessTest>(
      `/api/bounties/${encodeURIComponent(bountyId)}/access-test`,
      undefined,
      {
        headers: { "X-Owner-Key": key },
      },
    )
      .then((t) => {
        setTest(t);
        setForm(toForm(t));
      })
      .catch((e) => setError(errorText(e)));
  }, [bountyId, key, demo]);

  if (demo)
    return (
      <p className="bo-muted" data-testid="security-demo">
        Demo shop: test user and markers are set for you.
      </p>
    );
  if (key === undefined) return null;
  if (!key)
    return (
      <p className="bo-muted" data-testid="security-no-key">
        Open this page from the browser where you created the hunt to set up the
        security test.
      </p>
    );

  const set = (k: keyof Form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const t = await api<AccessTest>(
        `/api/bounties/${encodeURIComponent(bountyId)}/access-test`,
        {
          login_path: form.path.trim(),
          login_body: {
            [form.userKey.trim()]: form.userVal,
            [form.passKey.trim()]: form.passVal,
          },
        },
        { method: "PUT", headers: { "X-Owner-Key": key as string } },
      );
      setTest(t);
      await onChange();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  }

  return (
    <div data-testid="security-form">
      <p>
        Agents log in as your test user and try to see data it must not see.
        Hide these markers in places the test user must never reach. If an agent
        shows one, the leak is proven.
      </p>
      {test && (
        <ul className="bo-markers" data-testid="security-markers">
          {test.markers.map((m) => (
            <li key={m.index}>
              <strong>{m.label}</strong> <code>{m.value}</code>{" "}
              <CopyButton text={m.value} label={m.label} />
              <br />
              <small>{hint(m.label)}</small>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={save}>
        <label className="bo-field">
          Login URL path
          <input
            name="login_path"
            value={form.path}
            onChange={set("path")}
            required
          />
        </label>
        <label className="bo-field">
          Email or username field name
          <input
            name="user_key"
            value={form.userKey}
            onChange={set("userKey")}
            required
          />
        </label>
        <label className="bo-field">
          Email or username of the test user
          <input
            name="user_value"
            value={form.userVal}
            onChange={set("userVal")}
            required
          />
        </label>
        <label className="bo-field">
          Password field name
          <input
            name="pass_key"
            value={form.passKey}
            onChange={set("passKey")}
            required
          />
        </label>
        <label className="bo-field">
          Password of the test user
          <input
            name="pass_value"
            value={form.passVal}
            onChange={set("passVal")}
            required
          />
        </label>
        <button type="submit" className="bo-btn" disabled={busy}>
          {busy ? "Saving…" : "Save the security test"}
        </button>
      </form>
      {error && <Message kind="error" text={error} testId="security-error" />}
      {ready && (
        <p data-testid="security-ready">
          <strong>Security test ready</strong>
        </p>
      )}
    </div>
  );
}
