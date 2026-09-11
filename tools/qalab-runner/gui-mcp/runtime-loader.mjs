// The runner bundle includes this shared backend asset next to this loader.
// A source checkout reads the canonical file directly; no generated copy to drift.
import { existsSync } from "node:fs";
const bundled = new URL("./playwright-runtime.mjs", import.meta.url);
const source = new URL("../../../backend/app/services/playwright_runtime.mjs", import.meta.url);
export const { elementTextValue, createAutomationRuntime, responseArgsBeforeAction, toUrlMatcher, buildMockResponse } = await import(existsSync(bundled) ? bundled.href : source.href);
