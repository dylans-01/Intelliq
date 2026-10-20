import { describe, expect, it } from "vitest";
import { ColorRegistry } from "./colors";
import { escapeHtml, formatDate, formatDatePart, formatNumber, formatValue } from "./format";

describe("formatNumber", () => {
  it("formats currency, percent and compact values", () => {
    expect(formatNumber(1234567.8, { type: "currency", decimals: 0 })).toBe("$1,234,568");
    expect(formatNumber(1234567.8, { type: "currency" }, "real", { compact: true })).toBe("$1.2M");
    expect(formatNumber(-2500, { type: "currency" }, "real", { compact: true })).toBe("-$2.5K");
    expect(formatNumber(0.08123, { type: "percent", decimals: 1 })).toBe("8.1%");
    expect(formatNumber(42, null, "integer")).toBe("42");
    expect(formatNumber(3.14159, null)).toBe("3.14");
    expect(formatNumber(950, null, "real", { compact: true })).toBe("950");
  });
});

describe("dates", () => {
  it("labels truncated dates like Tableau", () => {
    expect(formatDate("2024-03-01", "month")).toBe("Mar 2024");
    expect(formatDate("2024-04-01", "quarter")).toBe("Q2 2024");
    expect(formatDate("2024-01-01", "year")).toBe("2024");
    expect(formatDate("2024-03-03", "week")).toBe("Week of Mar 3, 2024");
    expect(formatDate("2024-03-06")).toBe("Mar 6, 2024");
    expect(formatDate("2024-03-06 14:30:00")).toBe("Mar 6, 2024 14:30");
  });
  it("labels date parts", () => {
    expect(formatDatePart(3, "month")).toBe("Mar");
    expect(formatDatePart(1, "weekday")).toBe("Sun");
    expect(formatDatePart(2, "quarter")).toBe("Q2");
  });
});

describe("formatValue", () => {
  it("handles nulls by role", () => {
    expect(formatValue(null, { data_type: "real", role: "measure" })).toBe("–");
    expect(formatValue(null, { data_type: "string", role: "dimension" })).toBe("Null");
  });
});

describe("escapeHtml", () => {
  it("escapes markup in labels", () => {
    expect(escapeHtml(`<img src=x onerror="alert(1)">`)).toBe("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  });
});

describe("ColorRegistry", () => {
  it("keeps colours stable per entity and folds past eight", () => {
    const r = new ColorRegistry();
    expect(r.slot("region", "East")).toBe(0);
    expect(r.slot("region", "West")).toBe(1);
    expect(r.slot("region", "East")).toBe(0); // filtering West out never repaints East
    for (let i = 0; i < 10; i++) r.slot("sub", `m${i}`);
    expect(r.slot("sub", "m7")).toBe(7);
    expect(r.slot("sub", "m8")).toBe(-1);
  });
});
