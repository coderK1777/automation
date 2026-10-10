import Link from "next/link";
export default function NotFound() {
  return (
    <main className="fatal-error">
      <h1>Page not found</h1>
      <p>Choose a workspace from the dashboard.</p>
      <Link className="btn btn-primary" href="/">
        Open dashboard
      </Link>
    </main>
  );
}
