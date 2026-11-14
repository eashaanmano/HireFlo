import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { applyView, DataTable, toCsv, type Column } from "./DataTable";

interface Row { id: number; name: string; branch: string; cgpa: number | null }
const rows: Row[] = [
  { id: 1, name: "Asha", branch: "CSE", cgpa: 8.2 },
  { id: 2, name: "Bala", branch: "ECE", cgpa: 9.1 },
  { id: 3, name: "Chitra", branch: "CSE", cgpa: null },
];
const cols: Column<Row>[] = [
  { key: "name", header: "Name", value: (r) => r.name },
  { key: "branch", header: "Branch", value: (r) => r.branch, filter: true },
  { key: "cgpa", header: "CGPA", value: (r) => r.cgpa },
];

describe("applyView", () => {
  it("filters, searches and sorts with blanks last", () => {
    expect(applyView(rows, cols, "", { branch: "CSE" }, null).map((r) => r.id)).toEqual([1, 3]);
    expect(applyView(rows, cols, "bal", {}, null).map((r) => r.id)).toEqual([2]);
    expect(applyView(rows, cols, "", {}, { key: "cgpa", dir: "desc" }).map((r) => r.id)).toEqual([2, 1, 3]);
    expect(applyView(rows, cols, "", {}, { key: "cgpa", dir: "asc" }).map((r) => r.id)).toEqual([1, 2, 3]);
  });
});

describe("toCsv", () => {
  it("quotes commas and neutralises formula-looking cells", () => {
    const csv = toCsv([{ id: 9, name: "=HYPERLINK(1)", branch: "A, B", cgpa: 7 }], cols);
    expect(csv.split("\r\n")).toEqual(["Name,Branch,CGPA", "'=HYPERLINK(1),\"A, B\",7"]);
  });
});

describe("DataTable", () => {
  it("sorts when a header is clicked and filters by search", () => {
    render(<DataTable rows={rows} columns={cols} rowKey={(r) => r.id} />);
    fireEvent.click(screen.getByRole("button", { name: /CGPA/ }));
    fireEvent.click(screen.getByRole("button", { name: /CGPA/ }));
    const names = screen.getAllByRole("row").slice(1).map((tr: HTMLElement) => tr.textContent);
    expect(names[0]).toContain("Bala");
    fireEvent.change(screen.getByLabelText("Search table"), { target: { value: "chit" } });
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getByText("1 of 3 rows")).toBeTruthy();
  });
});
