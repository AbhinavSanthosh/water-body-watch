/**
 * Records microphone audio and encodes it as 16 kHz mono PCM WAV.
 *
 * Bhashini accepts wav/mp3/flac, but MediaRecorder produces webm on Chrome and
 * mp4 on Safari. Encoding WAV in the browser keeps the backend contract stable.
 */

const TARGET_SAMPLE_RATE = 16_000;

function mergeChunks(chunks: Float32Array[]): Float32Array {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return merged;
}

function downsample(
  input: Float32Array,
  inputRate: number,
  outputRate: number
): Float32Array {
  if (outputRate >= inputRate) {
    return input;
  }
  const ratio = inputRate / outputRate;
  const outputLength = Math.round(input.length / ratio);
  const output = new Float32Array(outputLength);
  let inputOffset = 0;

  for (let outputOffset = 0; outputOffset < outputLength; outputOffset += 1) {
    const nextInputOffset = Math.round((outputOffset + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (let index = inputOffset; index < nextInputOffset && index < input.length; index += 1) {
      sum += input[index];
      count += 1;
    }
    output[outputOffset] = count > 0 ? sum / count : 0;
    inputOffset = nextInputOffset;
  }
  return output;
}

function writeAscii(view: DataView, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}

export function encodeWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  writeAscii(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(view, 8, 'WAVE');
  writeAscii(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, 'data');
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return buffer;
}

export class WavRecorder {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private chunks: Float32Array[] = [];
  private active = false;

  get isRecording(): boolean {
    return this.active;
  }

  async start(): Promise<void> {
    if (this.active) {
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('This browser does not expose microphone access.');
    }

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });

    const AudioContextClass =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.context = new AudioContextClass();
    await this.context.resume();
    this.source = this.context.createMediaStreamSource(this.stream);
    this.processor = this.context.createScriptProcessor(4096, 1, 1);
    this.chunks = [];
    this.active = true;

    this.processor.onaudioprocess = (event: AudioProcessingEvent) => {
      if (!this.active) {
        return;
      }
      this.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    };

    this.source.connect(this.processor);
    this.processor.connect(this.context.destination);
  }

  async stop(): Promise<Blob> {
    if (!this.active) {
      throw new Error('Recording has not started.');
    }
    this.active = false;
    const sampleRate = this.context?.sampleRate ?? 48_000;
    const merged = mergeChunks(this.chunks);
    this.teardown();

    const downsampled = downsample(merged, sampleRate, TARGET_SAMPLE_RATE);
    const wav = encodeWav(downsampled, TARGET_SAMPLE_RATE);
    return new Blob([wav], { type: 'audio/wav' });
  }

  cancel(): void {
    this.active = false;
    this.chunks = [];
    this.teardown();
  }

  private teardown(): void {
    this.processor?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    void this.context?.close();
    this.processor = null;
    this.source = null;
    this.stream = null;
    this.context = null;
  }
}
