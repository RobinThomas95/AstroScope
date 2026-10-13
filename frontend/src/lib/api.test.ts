import { describe, expect, it } from "vitest";
import { formatDetail } from "./api";

describe("formatDetail", () => {
  it("keeps a string detail", () => {
    expect(formatDetail({ detail: "boom" }, "Unprocessable Entity")).toBe(
      "boom",
    );
  });
  it("renders FastAPI validation errors readably", () => {
    const body = {
      detail: [
        {
          loc: ["query", "max_mb"],
          msg: "Input should be less than or equal to 5000",
          type: "less_than_equal",
        },
      ],
    };
    expect(formatDetail(body, "Unprocessable Entity")).toBe(
      "Invalid request: max_mb: Input should be less than or equal to 5000",
    );
  });
  it("falls back to the status text", () => {
    expect(formatDetail(null, "Bad Gateway")).toBe("Bad Gateway");
    expect(formatDetail({ detail: [] }, "Bad Gateway")).toBe("Bad Gateway");
  });
});
