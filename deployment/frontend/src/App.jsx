import { useEffect, useRef, useState } from "react";

const MAX_RECORDING_SECONDS = 10;

async function fetchJson(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed with ${response.status}`);
  }
  return response.json();
}

function groupByDataset(samples) {
  const grouped = new Map();
  samples.forEach((sample) => {
    const key = sample.dataset || "Samples";
    if (!grouped.has(key)) {
      grouped.set(key, []);
    }
    grouped.get(key).push(sample);
  });
  return Array.from(grouped.entries());
}

function StatusPill({ status }) {
  return <span className={`status-pill status-${status}`}>{status}</span>;
}

function SampleCard({ sample, active, onUse }) {
  return (
    <article className={`sample-card ${active ? "active" : ""}`}>
      <div className="sample-card-top">
        <div>
          <p className="sample-dataset">{sample.dataset}</p>
          <h3>{sample.label}</h3>
        </div>
        <button className="ghost-button compact" onClick={() => onUse(sample)} type="button">
          Use this voice
        </button>
      </div>
      <p className="sample-reference-text">{sample.reference_text}</p>
      {sample.prompt_audio_url ? <audio controls preload="none" src={sample.prompt_audio_url} /> : null}
    </article>
  );
}

export default function App() {
  const [samples, setSamples] = useState([]);
  const [selectedSample, setSelectedSample] = useState(null);
  const [referencePrompts, setReferencePrompts] = useState([]);
  const [referencePrompt, setReferencePrompt] = useState("");
  const [targetText, setTargetText] = useState("");
  const [sourceMode, setSourceMode] = useState("sample");
  const [job, setJob] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [recordingSupported, setRecordingSupported] = useState(true);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [recordedBlob, setRecordedBlob] = useState(null);
  const [recordedUrl, setRecordedUrl] = useState("");

  const mediaRecorderRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const recordingTimerRef = useRef(null);
  const recordingStopRef = useRef(null);

  useEffect(() => {
    setRecordingSupported(
      typeof window !== "undefined" &&
        typeof navigator !== "undefined" &&
        Boolean(navigator.mediaDevices?.getUserMedia) &&
        typeof window.MediaRecorder !== "undefined",
    );
  }, []);

  useEffect(() => {
    fetchJson("/samples")
      .then((data) => {
        const nextSamples = data.samples || [];
        setSamples(nextSamples);
        if (nextSamples.length) {
          setSelectedSample(nextSamples[0]);
        }
      })
      .catch((err) => setError(err.message));

    fetchJson("/reference-prompts")
      .then((data) => {
        setReferencePrompts(data.prompts || []);
        setReferencePrompt(data.default_prompt || data.prompts?.[0] || "");
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

  useEffect(() => {
    return () => {
      if (recordedUrl) {
        URL.revokeObjectURL(recordedUrl);
      }
      if (recordingTimerRef.current) {
        window.clearInterval(recordingTimerRef.current);
      }
      if (recordingStopRef.current) {
        window.clearTimeout(recordingStopRef.current);
      }
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      }
    };
  }, [recordedUrl]);

  function choosePrompt() {
    if (!referencePrompts.length) {
      return;
    }
    const candidates = referencePrompts.filter((item) => item !== referencePrompt);
    const pool = candidates.length ? candidates : referencePrompts;
    const next = pool[Math.floor(Math.random() * pool.length)];
    setReferencePrompt(next);
  }

  function useSample(sample) {
    setSelectedSample(sample);
    setSourceMode("sample");
  }

  function clearRecordingTimers() {
    if (recordingTimerRef.current) {
      window.clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    if (recordingStopRef.current) {
      window.clearTimeout(recordingStopRef.current);
      recordingStopRef.current = null;
    }
  }

  function resetRecordedAudio() {
    if (recordedUrl) {
      URL.revokeObjectURL(recordedUrl);
    }
    setRecordedBlob(null);
    setRecordedUrl("");
    setRecordingSeconds(0);
  }

  async function startRecording() {
    if (!recordingSupported || isRecording) {
      return;
    }

    setError("");
    resetRecordedAudio();

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;

      const mimeTypeCandidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
      ];
      const mimeType =
        mimeTypeCandidates.find((candidate) => window.MediaRecorder.isTypeSupported(candidate)) ||
        "";

      const recorder = new window.MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      const chunks = [];

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          chunks.push(event.data);
        }
      };

      recorder.onstop = () => {
        clearRecordingTimers();
        setIsRecording(false);
        const blobType = mimeType || "audio/webm";
        const blob = new Blob(chunks, { type: blobType });
        const url = URL.createObjectURL(blob);
        setRecordedBlob(blob);
        setRecordedUrl(url);
        if (mediaStreamRef.current) {
          mediaStreamRef.current.getTracks().forEach((track) => track.stop());
          mediaStreamRef.current = null;
        }
      };

      mediaRecorderRef.current = recorder;
      setRecordingSeconds(0);
      setIsRecording(true);
      recorder.start();

      recordingTimerRef.current = window.setInterval(() => {
        setRecordingSeconds((value) => Math.min(value + 1, MAX_RECORDING_SECONDS));
      }, 1000);

      recordingStopRef.current = window.setTimeout(() => {
        if (mediaRecorderRef.current?.state === "recording") {
          mediaRecorderRef.current.stop();
        }
      }, MAX_RECORDING_SECONDS * 1000);
    } catch (err) {
      setError(err.message || "Could not access the microphone.");
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((track) => track.stop());
        mediaStreamRef.current = null;
      }
    }
  }

  function stopRecording() {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
  }

  async function createJob() {
    setCreating(true);
    setError("");

    try {
      const formData = new FormData();
      formData.append("text", targetText);
      formData.append("source_type", sourceMode === "sample" ? "sample" : "recorded_audio");

      if (sourceMode === "sample") {
        if (!selectedSample) {
          throw new Error("Choose one of the sample voices first.");
        }
        formData.append("sample_id", selectedSample.id);
      } else {
        if (!referencePrompt.trim()) {
          throw new Error("Write the phrase you are about to read before sending the recording.");
        }
        if (!recordedBlob) {
          throw new Error("Record a reference audio before sending the job.");
        }
        const extension = recordedBlob.type.includes("ogg") ? "ogg" : "webm";
        formData.append("prompt_text", referencePrompt.trim());
        formData.append("input_origin", "recorded");
        formData.append("prompt_audio", recordedBlob, `recorded-reference.${extension}`);
      }

      const response = await fetch("/jobs", {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `Request failed with ${response.status}`);
      }
      setJob(await response.json());
    } catch (err) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  }

  const groupedSamples = groupByDataset(samples);
  const selectedSourceLabel =
    sourceMode === "sample"
      ? selectedSample?.label || "No sample selected"
      : recordedBlob
        ? "Recorded voice"
        : "No recording yet";

  return (
    <main className="page-shell">
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Hybrid EC2 + local worker demo</p>
          <h1>ZipVoice-CA</h1>
          <p className="lede">
            Browse real Catalan reference voices, then synthesize your own target text using one of
            the curated samples or a browser recording captured live for the worker pipeline.
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
            <h2>Sample Gallery</h2>
            <span>{samples.length} reference voices</span>
          </div>
          <div className="samples-list">
            {groupedSamples.map(([dataset, items]) => (
              <section key={dataset} className="dataset-block">
                <div className="dataset-heading">
                  <h3>{dataset}</h3>
                  <span>{items.length} samples</span>
                </div>
                <div className="dataset-samples">
                  {items.map((sample) => (
                    <SampleCard
                      key={sample.id}
                      sample={sample}
                      active={selectedSample?.id === sample.id && sourceMode === "sample"}
                      onUse={useSample}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>

        <div className="detail-panel">
          <div className="detail-card">
            <div className="section-header">
              <h2>Inference Composer</h2>
              {job ? <StatusPill status={job.status} /> : null}
            </div>

            <label className="field-block">
              <span className="detail-label">Target text</span>
              <textarea
                className="text-input text-area"
                placeholder="Escriu aquí el text que vols sintetitzar..."
                value={targetText}
                onChange={(event) => setTargetText(event.target.value)}
                rows={5}
              />
            </label>

            <div className="mode-switch">
              <button
                className={`mode-button ${sourceMode === "sample" ? "active" : ""}`}
                onClick={() => setSourceMode("sample")}
                type="button"
              >
                Use sample voice
              </button>
              <button
                className={`mode-button ${sourceMode === "record" ? "active" : ""}`}
                onClick={() => setSourceMode("record")}
                type="button"
              >
                Record voice
              </button>
            </div>

            {sourceMode === "sample" ? (
              <div className="source-panel">
                {selectedSample ? (
                  <>
                    <p className="detail-label">Selected sample</p>
                    <h3>{selectedSample.label}</h3>
                    <p className="sample-reference-text">{selectedSample.reference_text}</p>
                    {selectedSample.prompt_audio_url ? (
                      <audio controls preload="none" src={selectedSample.prompt_audio_url} />
                    ) : null}
                  </>
                ) : (
                  <p className="muted">Choose a sample voice from the gallery.</p>
                )}
              </div>
            ) : (
              <div className="source-panel">
                <div className="panel-row">
                  <p className="detail-label">Reference phrase</p>
                  <button className="ghost-button compact" onClick={choosePrompt} type="button">
                    Regenerate phrase
                  </button>
                </div>
                <textarea
                  className="text-input text-area"
                  value={referencePrompt}
                  onChange={(event) => setReferencePrompt(event.target.value)}
                  rows={4}
                />

                <div className="recorder-row">
                  <div>
                    <p className="detail-label">Recording</p>
                    <p className="muted">
                      Read the phrase above. You can re-record as many times as you want. Max{" "}
                      {MAX_RECORDING_SECONDS}s.
                    </p>
                  </div>
                  <div className="recorder-actions">
                    {!isRecording ? (
                      <button
                        className="primary-button secondary"
                        disabled={!recordingSupported}
                        onClick={startRecording}
                        type="button"
                      >
                        {recordedBlob ? "Record again" : "Start recording"}
                      </button>
                    ) : (
                      <button className="primary-button secondary" onClick={stopRecording} type="button">
                        Stop recording
                      </button>
                    )}
                    <span className={`recording-badge ${isRecording ? "live" : ""}`}>
                      {isRecording ? `Recording ${recordingSeconds}s` : recordedBlob ? "Last take ready" : "Waiting"}
                    </span>
                  </div>
                </div>

                {!recordingSupported ? (
                  <p className="error-copy">This browser does not support microphone recording.</p>
                ) : null}

                {recordedUrl ? <audio controls preload="none" src={recordedUrl} /> : null}
              </div>
            )}

            <button className="primary-button" onClick={createJob} disabled={creating || isRecording}>
              {creating ? "Creating job..." : "Run inference"}
            </button>
          </div>

          <div className="result-card">
            <div className="section-header">
              <h2>Result</h2>
              {job?.metadata?.cache ? <span className="cache-note">cached fallback</span> : null}
            </div>
            {!job ? <p className="muted">Create a job to watch the worker process it.</p> : null}
            {job?.error ? <p className="error-copy">{job.error}</p> : null}
            {job?.result_url ? <audio controls preload="none" src={job.result_url} /> : null}
            {job ? (
              <dl className="job-meta">
                <div>
                  <dt>Source</dt>
                  <dd>{job.source_sample?.label || selectedSourceLabel}</dd>
                </div>
                <div>
                  <dt>Origin</dt>
                  <dd>{job.input_origin || job.source_type}</dd>
                </div>
                <div>
                  <dt>Target text</dt>
                  <dd>{job.target_text}</dd>
                </div>
                <div>
                  <dt>Prompt text</dt>
                  <dd>{job.prompt_text}</dd>
                </div>
                <div>
                  <dt>Job ID</dt>
                  <dd>{job.id}</dd>
                </div>
                <div>
                  <dt>Worker</dt>
                  <dd>{job.worker_id || "pending assignment"}</dd>
                </div>
              </dl>
            ) : null}
          </div>
        </div>
      </section>
    </main>
  );
}
