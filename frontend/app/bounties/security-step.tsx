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

type Form = { loginUrl: string; email: string; password: string };

const EMPTY: Form = { loginUrl: "", email: "", password: "" };

function toForm(t: AccessTest): Form {
  return {
    loginUrl: t.login_url ?? "",
    email: t.email ?? "",
    password: t.password ?? "",
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
          login_url: form.loginUrl.trim(),
          email: form.email.trim(),
          password: form.password,
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
          Login page URL
          <input
            name="login_url"
            type="url"
            placeholder="https://app.example.com/sign-in"
            value={form.loginUrl}
            onChange={set("loginUrl")}
            required
          />
        </label>
        <label className="bo-field">
          Email of the test user
          <input
            name="email"
            autoComplete="off"
            value={form.email}
            onChange={set("email")}
            required
          />
        </label>
        <label className="bo-field">
          Password of the test user
          <input
            name="password"
            autoComplete="off"
            value={form.password}
            onChange={set("password")}
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
