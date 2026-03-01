/**
 * Ambient type declarations for the vis-network library loaded via CDN.
 *
 * Provides type information for the global `vis` namespace used by
 * {@link LinkChartManager} for interactive graph visualization.
 * The library is loaded as a UMD global via a `<script>` tag.
 */

declare namespace vis {
  class DataSet<T = unknown> {
    constructor(data?: T[]);
    add(data: T | T[]): void;
    update(data: T | T[]): void;
    remove(id: string | number | Array<string | number>): void;
    get(): T[];
    get(id: string | number): T | null;
  }

  interface NetworkOptions {
    nodes?: Record<string, unknown>;
    edges?: Record<string, unknown>;
    physics?: Record<string, unknown> | boolean;
    interaction?: Record<string, unknown>;
    layout?: Record<string, unknown>;
    [key: string]: unknown;
  }

  class Network {
    constructor(container: HTMLElement, data: { nodes: DataSet; edges: DataSet }, options?: NetworkOptions);
    on(event: string, callback: (params: Record<string, unknown>) => void): void;
    getNodeAt(position: { x: number; y: number }): string | number | undefined;
    getEdgeAt(position: { x: number; y: number }): string | number | undefined;
    destroy(): void;
    fit(): void;
    stabilize(): void;
  }
}
