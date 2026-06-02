import React, { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Flame, Zap } from "lucide-react";

const LOGIN_BG =
  "https://static.prod-images.emergentagent.com/jobs/ed4e7d81-8953-45de-a23c-d48e941ecd1d/images/2f6ca7a6762694d8e1ea0a0988b4619f404b2c526a4d4d50bdadd00248948ac6.png";

export default function Login() {
  const { user, error, setError, login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState("login"); // "login" | "register"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);

  if (user && user !== false && user !== null) {
    return <Navigate to="/chat" replace />;
  }

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    const ok =
      mode === "login"
        ? await login(email, password)
        : await register(email, password, name);
    setLoading(false);
    if (ok) navigate("/chat");
  };

  return (
    <div className="min-h-screen w-full flex">
      {/* Left: hero image */}
      <div
        className="hidden md:flex md:w-1/2 relative overflow-hidden"
        style={{
          backgroundImage: `url(${LOGIN_BG})`,
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
        data-testid="login-hero-image"
      >
        <div className="absolute inset-0 bg-black/60" />
        <div className="relative z-10 flex flex-col justify-between p-12 w-full">
          <div className="flex items-center gap-3">
            <Flame className="w-8 h-8 text-[#ff2a6d]" />
            <span className="font-heading font-black text-xl tracking-tight">
              UNCHAINED FORGE
            </span>
          </div>
          <div>
            <h1 className="font-heading text-4xl lg:text-6xl font-black leading-[0.95] tracking-tighter">
              FORGE
              <br />
              <span className="text-[#ffd700]">YOUR OWN</span>
              <br />
              <span className="text-[#ff2a6d]">A.I.</span>
            </h1>
            <p className="mt-6 text-gray-300 max-w-md text-lg">
              Powered by{" "}
              <span className="font-mono text-[#05d9e8]">Gemini 3 Pro</span>.
              Zero filters. Zero dollars. Pure raw intelligence at your
              fingertips.
            </p>
            <div className="mt-8 flex items-center gap-2 text-xs uppercase tracking-[0.3em] text-gray-400">
              <Zap className="w-3 h-3 text-[#ffd700]" />
              <span>gemini3_unchained_zerodollar_forge</span>
            </div>
          </div>
        </div>
      </div>

      {/* Right: form */}
      <div className="w-full md:w-1/2 flex items-center justify-center p-6 md:p-12 bg-[#050505]">
        <div className="w-full max-w-md">
          <div className="mb-10">
            <div className="text-xs uppercase tracking-[0.3em] text-[#ffd700] font-bold mb-3">
              {mode === "login" ? "// access the forge" : "// join the forge"}
            </div>
            <h2 className="font-heading text-4xl font-black tracking-tighter">
              {mode === "login" ? "ENTER" : "CREATE"}
              <span className="text-[#ff2a6d]">.</span>
            </h2>
            <p className="text-gray-400 mt-3">
              {mode === "login"
                ? "Sign in to unleash Gemini 3 Pro."
                : "Mint a new account, no friction."}
            </p>
          </div>

          <form onSubmit={onSubmit} className="space-y-5">
            {mode === "register" && (
              <div>
                <label className="block text-xs uppercase tracking-[0.2em] font-bold text-gray-400 mb-2">
                  Handle
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="input-brutal"
                  placeholder="your_alias"
                  data-testid="register-name-input"
                />
              </div>
            )}
            <div>
              <label className="block text-xs uppercase tracking-[0.2em] font-bold text-gray-400 mb-2">
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="input-brutal"
                placeholder="you@theforge.dev"
                required
                data-testid="auth-email-input"
              />
            </div>
            <div>
              <label className="block text-xs uppercase tracking-[0.2em] font-bold text-gray-400 mb-2">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input-brutal"
                placeholder="••••••••"
                required
                minLength={6}
                data-testid="auth-password-input"
              />
            </div>

            {error && (
              <div
                className="border-2 border-[#ff2a6d] bg-[#ff2a6d]/10 text-[#ff2a6d] p-3 text-sm font-mono"
                data-testid="auth-error"
              >
                {error}
              </div>
            )}

            <button
              type="submit"
              className="btn-primary w-full"
              disabled={loading}
              data-testid="auth-submit-btn"
            >
              {loading
                ? "Forging..."
                : mode === "login"
                  ? "Sign In →"
                  : "Create Account →"}
            </button>
          </form>

          <div className="mt-8 text-center text-sm text-gray-400">
            {mode === "login" ? (
              <>
                No account?{" "}
                <button
                  className="text-[#ffd700] font-bold hover:text-[#ff2a6d] underline underline-offset-4"
                  onClick={() => {
                    setMode("register");
                    setError("");
                  }}
                  data-testid="switch-to-register-btn"
                >
                  Register
                </button>
              </>
            ) : (
              <>
                Already here?{" "}
                <button
                  className="text-[#ffd700] font-bold hover:text-[#ff2a6d] underline underline-offset-4"
                  onClick={() => {
                    setMode("login");
                    setError("");
                  }}
                  data-testid="switch-to-login-btn"
                >
                  Sign in
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
