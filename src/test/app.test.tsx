import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import App from "../App";

describe("MIIT Rover delivery workflows", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.pushState({}, "", "/");
  });

  it("renders the operations overview", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "Operations overview" })).toBeTruthy();
    expect(screen.getByText("The campus fleet is operating normally.")).toBeTruthy();
  });

  it("creates a valid delivery request", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("link", { name: "New delivery" }));

    await user.type(screen.getByPlaceholderText("e.g. Signed laboratory documents"), "Sensor calibration kit");
    await user.type(screen.getByPlaceholderText("Full name or office"), "Embedded Systems Lab");
    await user.type(screen.getByPlaceholderText("+95 9 ..."), "+95 9 777 555 111");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Submit delivery request" }));

    await waitFor(() => expect(window.location.pathname).toBe("/deliveries"));
    expect(await screen.findByText("MIIT-1051", {}, { timeout: 5000 })).toBeTruthy();
    expect(screen.getByText("Sensor calibration kit")).toBeTruthy();
    await waitFor(() => {
      const saved = JSON.parse(localStorage.getItem("miit-rover-deliveries-v1") ?? "[]");
      expect(saved[0]).toMatchObject({
        requesterName: "Demo User",
        requesterEmail: "user@miit.edu.mm",
      });
    });
  }, 15_000);

  it("approves, assigns and dispatches a waiting request", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("link", { name: "Dispatch center" }));
    await user.click(screen.getByText("MIIT-1050"));

    await user.click(screen.getByRole("button", { name: "Approve request" }));
    expect(await screen.findByRole("button", { name: "Assign selected robot" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Assign selected robot" }));
    expect(await screen.findByRole("button", { name: "Dispatch mission" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Dispatch mission" }));
    expect(await screen.findByRole("button", { name: "Advance demo checkpoint" })).toBeTruthy();
  });

  it("requires confirmation before an emergency stop", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("link", { name: "Robot fleet" }));
    await user.click(screen.getByText("Emergency stop"));
    expect(screen.getByRole("heading", { name: "Emergency-stop Rover 01?" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Confirm emergency stop" }));
    expect(await screen.findByText("Fault")).toBeTruthy();
  });
});
