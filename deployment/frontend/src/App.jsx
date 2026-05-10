import { useEffect, useState } from "react";

async function fetchJson(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed with ${response.status}`);
  }
  return response.json();
}

function SampleCard({ sample, active, onSelect }) {
  return (
    <button
      className={`sample-card ${active ? "active" : ""}`}
      onClick={() => onSelect(sample)}
      type="button"
    >
      <span className="sample-label">{sample.label}</span>
      <span className="sample-id">{sample.id}</span>
      <span className="sample-text">{sample.text}</span>
    </button>
  );
}

function StatusPill({ status }) {
  return <span className={`status-pill status-${status}`}>{status}</span>;
}

export default function App() {
  const [samples, setSamples] = useState([]);
  const [selected, setSelected] = useState(null);
  const [job, setJob] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    fetchJson("/samples")
      .then((data) => {
        setSamples(data.samples || []);
        if (data.samples?.length) {
          setSelected(data.samples[0]);
        }
      })
      .catch((err) => setError(err.message));

    fetchJson("/health")
      .then(setHealth)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!job || !["pending", "processing"].includes(job.status)) {
      return undefined;
    }
    const interval = window.setInterval(() => {
      fetchJson(`/jobs/${job.id}`)
        .then(setJob)
        .catch((err) => setError(err.message));
    }, 2500);
    return () => window.clearInterval(interval);
  }, [job]);

  async function createJob() {
    if (!selected) {
      return;
    }
    setCreating(true);
    setError("");
    try {
      const nextJob = await fetchJson("/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sample_id: selected.id }),
      });
      setJob(nextJob);
    } catch (err) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  }

  return (
    <main className="page-shell">
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Hybrid EC2 + local worker demo</p>
          <h1>ZipVoice-CA</h1>
          <p className="lede">
            Public API and frontend on EC2, real Catalan TTS inference on your local machine, and
            S3-backed samples plus cached fallback results for a stable demo.
          </p>
        </div>
        <div className="hero-panel">
          <div className="health-row">
            <span>Mode</span>
            <strong>{health?.demo_mode || "loading"}</strong>
          </div>
          <div className="health-row">
            <span>S3</span>
            <strong>{health?.s3_enabled ? "connected" : "not configured"}</strong>
          </div>
          <div className="health-row">
            <span>Worker</span>
            <strong>{health?.worker_last_seen_worker_id || "waiting"}</strong>
          </div>
        </div>
      </section>

      {error ? <section className="error-banner">{error}</section> : null}

      <section className="content-grid">
        <div className="samples-panel">
          <div className="section-header">
            <h2>Sample Library</h2>
            <span>{samples.length} ready cases</span>
          </div>
          <div className="samples-list">
            {samples.map((sample) => (
              <SampleCard
                key={sample.id}
                sample={sample}
                active={selected?.id === sample.id}
                onSelect={setSelected}
              />
            ))}
          </div>
        </div>

        <div className="detail-panel">
          <div className="section-header">
            <h2>Inference Request</h2>
            {job ? <StatusPill status={job.status} /> : null}
          </div>

          {selected ? (
            <div className="detail-card">
              <p className="detail-label">Prompt text</p>
              <p>{selected.prompt_text}</p>
              <p className="detail-label">Target text</p>
              <p>{selected.text}</p>
              {selected.prompt_audio_url ? (
                <>
                  <p className="detail-label">Reference audio</p>
                  <audio controls src={selected.prompt_audio_url} />
                </>
              ) : (
                <p className="muted">This sample does not yet expose a prompt audio URL.</p>
              )}
              <button className="primary-button" onClick={createJob} disabled={creating}>
                {creating ? "Creating job..." : "Run inference"}
              </button>
            </div>
          ) : (
            <div className="detail-card">
              <p>No samples available yet.</p>
            </div>
          )}

          <div className="result-card">
            <div className="section-header">
              <h2>Result</h2>
              {job?.metadata?.cache ? <span className="cache-note">cached fallback</span> : null}
            </div>
            {!job ? <p className="muted">Create a job to watch the worker process it.</p> : null}
            {job?.error ? <p className="error-copy">{job.error}</p> : null}
            {job?.result_url ? <audio controls src={job.result_url} /> : null}
            {job ? (
              <dl className="job-meta">
                <div>
                  <dt>Job ID</dt>
                  <dd>{job.id}</dd>
                </div>
                <div>
                  <dt>Worker</dt>
                  <dd>{job.worker_id || "pending assignment"}</dd>
                </div>
                <div>
                  <dt>Created</dt>
                  <dd>{job.created_at}</dd>
                </div>
              </dl>
            ) : null}
          </div>
        </div>
      </section>
    </main>
  );
}
