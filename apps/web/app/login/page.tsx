"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { BarChart3, ArrowRight, Loader2, ShieldCheck } from "lucide-react";
import { api, setCsrfToken } from "@/lib/api";
import type { User } from "@/lib/types";
import { Alert, Button, Field, Input } from "@/components/ui";
const schema = z.object({
  username: z.string().min(1, "Enter your username"),
  password: z.string().min(1, "Enter your password"),
  code: z.string().optional(),
  recovery_code: z.string().optional(),
});
export default function Login() {
  const router = useRouter();
  const client = useQueryClient();
  const [checking, setChecking] = useState(true);
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });
  useEffect(() => {
    api<User>("/auth/me")
      .then((user) => {
        setCsrfToken(user.csrf_token);
        client.setQueryData(["/auth/me"], user);
        router.replace("/");
      })
      .catch(() => setChecking(false));
  }, [client, router]);
  const login = useMutation({
    mutationFn: (body: z.infer<typeof schema>) =>
      api<User>("/auth/login", {
        method: "POST",
        body: {
          ...body,
          code: body.code || undefined,
          recovery_code: body.recovery_code || undefined,
        },
      }),
    onSuccess: (user) => {
      setCsrfToken(user.csrf_token);
      client.setQueryData(["/auth/me"], user);
      router.replace("/");
    },
  });
  return (
    <main className="login-page">
      <section className="login-card">
        <div className="login-brand">
          <div className="brand-mark">
            <BarChart3 size={21} />
          </div>
          <div>
            <div className="brand-name">
              TradeBot <span>Pro</span>
            </div>
            <div className="brand-sub">Delta Exchange India</div>
          </div>
        </div>
        <h1>Welcome to your workspace</h1>
        <p>Sign in to research strategies and manage isolated paper trading.</p>
        {login.error && <Alert tone="red">{login.error.message}</Alert>}
        <form onSubmit={handleSubmit((data) => login.mutate(data))}>
          <Field label="Username" error={errors.username?.message}>
            <Input
              autoComplete="username"
              placeholder="Your operator username"
              {...register("username")}
            />
          </Field>
          <Field label="Password" error={errors.password?.message}>
            <Input
              type="password"
              autoComplete="current-password"
              placeholder="Your operator password"
              {...register("password")}
            />
          </Field>
          <Field label="Authenticator code (if enrolled)">
            <Input
              inputMode="numeric"
              autoComplete="one-time-code"
              {...register("code")}
            />
          </Field>
          <details className="login-recovery">
            <summary>Use a recovery code</summary>
            <Field label="Single-use recovery code">
              <Input autoComplete="off" {...register("recovery_code")} />
            </Field>
          </details>
          <Button disabled={login.isPending || checking} type="submit">
            {login.isPending || checking ? (
              <Loader2 size={15} className="spin" />
            ) : (
              <ArrowRight size={15} />
            )}
            Sign in
          </Button>
        </form>
        <div className="login-footer">
          <ShieldCheck
            size={13}
            style={{
              display: "inline",
              verticalAlign: "middle",
              marginRight: 5,
            }}
          />
          Secure session · Live execution disabled by default
        </div>
      </section>
    </main>
  );
}
