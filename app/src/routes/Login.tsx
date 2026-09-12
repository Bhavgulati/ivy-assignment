import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../state/auth";

const DEMO = ["demo1@ivy.homes", "demo2@ivy.homes", "demo3@ivy.homes"];

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState(DEMO[0]);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      nav("/listings", { replace: true });
    } catch (e: any) {
      setError(e?.message ?? "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="login">
        <h1>Chennai property</h1>
        <p className="sub">
          Sign in with one of the three demo accounts. Saved listings are kept
          per account.
        </p>

        <label>
          Account
          <select value={email} onChange={(e) => setEmail(e.target.value)}>
            {DEMO.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>

        <label>
          Password
          <input
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && password && submit()}
            placeholder="issued with the API key"
          />
        </label>

        {error ? <div className="errorbox">{error}</div> : null}

        <button disabled={busy || !password} onClick={submit}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <p className="fineprint">
          The access token lasts 15 minutes, not the 24 hours the API reference
          claims. This app refreshes it in the background, so the session keeps
          working and survives a page reload.
        </p>
      </div>
    </div>
  );
}
