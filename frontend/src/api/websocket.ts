import { EventMessage } from '../types/orchestrator';

type EventListener = (event: EventMessage) => void;

class OrchestratorWebSocket {
  private ws: WebSocket | null = null;
  private listeners: Set<EventListener> = new Set();
  private reconnectInterval = 3000;
  private reconnectTimer: any = null;
  private isConnected = false;
  private url: string;

  constructor() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    this.url = `${protocol}//${host}/ws/events`;
  }

  public connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.isConnected = true;
        console.log('[WS] Connected to Orchestrator Event Bus');
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.ws.onmessage = (event) => {
        try {
          const parsed: EventMessage = JSON.parse(event.data);
          this.notify(parsed);
        } catch (err) {
          console.error('[WS] Failed to parse event JSON:', event.data, err);
        }
      };

      this.ws.onclose = () => {
        this.isConnected = false;
        console.warn('[WS] Connection closed. Retrying in 3s...');
        this.scheduleReconnect();
      };

      this.ws.onerror = (err) => {
        console.error('[WS] WebSocket error:', err);
        if (this.ws) {
          this.ws.close();
        }
      };
    } catch (err) {
      console.error('[WS] Initialization error:', err);
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect() {
    if (!this.reconnectTimer) {
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.connect();
      }, this.reconnectInterval);
    }
  }

  public subscribe(listener: EventListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private notify(event: EventMessage) {
    this.listeners.forEach((listener) => {
      try {
        listener(event);
      } catch (err) {
        console.error('[WS] Listener error:', err);
      }
    });
  }

  public getStatus(): boolean {
    return this.isConnected;
  }
}

export const orchestratorWS = new OrchestratorWebSocket();
