import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { NewDeliveryInput } from "../types";

const mocks = vi.hoisted(() => ({
  getUser: vi.fn(),
  profileRoleSingle: vi.fn(),
  requesterProfileMaybeSingle: vi.fn(),
  deliveryRowsOrder: vi.fn(),
  robotRowsOrder: vi.fn(),
  notificationSelect: vi.fn(),
  notificationRowsOrder: vi.fn(),
  notificationRowsLimit: vi.fn(),
  deliveryInsert: vi.fn(),
  deliveryInsertSingle: vi.fn(),
  rpc: vi.fn(),
  channelOn: vi.fn(),
  channelSubscribe: vi.fn(),
  removeChannel: vi.fn(),
}));

vi.mock("../lib/supabase", () => {
  const channel = {
    on: (...args: unknown[]) => {
      mocks.channelOn(...args);
      return channel;
    },
    subscribe: () => {
      mocks.channelSubscribe();
      return channel;
    },
  };

  return {
    cloudEnabled: true,
    backendMode: "Supabase cloud",
    supabase: {
      auth: { getUser: mocks.getUser },
      from: (table: string) => {
        if (table === "profiles") {
          return {
            select: (columns: string) => ({
              eq: () => columns === "role"
                ? { single: mocks.profileRoleSingle }
                : { maybeSingle: mocks.requesterProfileMaybeSingle },
            }),
          };
        }
        if (table === "deliveries") {
          return {
            select: () => ({ order: mocks.deliveryRowsOrder }),
            insert: (payload: Record<string, unknown>) => mocks.deliveryInsert(payload),
          };
        }
        if (table === "robots") {
          return { select: () => ({ order: mocks.robotRowsOrder }) };
        }
        if (table === "notifications") {
          return {
            select: (columns: string) => {
              mocks.notificationSelect(columns);
              return {
                order: (column: string, options: Record<string, unknown>) => {
                  mocks.notificationRowsOrder(column, options);
                  return { limit: mocks.notificationRowsLimit };
                },
              };
            },
          };
        }
        throw new Error(`Unexpected Supabase table: ${table}`);
      },
      rpc: mocks.rpc,
      channel: () => channel,
      removeChannel: mocks.removeChannel,
    },
  };
});

import { AppProvider, useApp } from "../context/AppContext";

const deliveryInput: NewDeliveryInput = {
  sourceId: "loc-fcs",
  destinationId: "loc-data",
  itemName: "Authenticated profile test",
  category: "Documents",
  weightKg: 1,
  priority: "NORMAL",
  recipientName: "Data Center",
  recipientPhone: "+95 9 111 222 333",
};

function CreateDeliveryHarness() {
  const { createDelivery } = useApp();
  const [result, setResult] = useState("");

  const create = async () => {
    try {
      await createDelivery(deliveryInput);
      setResult("created");
    } catch (error) {
      setResult(error instanceof Error ? error.message : "failed");
    }
  };

  return (
    <>
      <button onClick={() => void create()}>Create cloud delivery</button>
      <output>{result}</output>
    </>
  );
}

function NotificationsHarness() {
  const { notifications, markNotificationsRead } = useApp();

  return (
    <>
      <output data-testid="notification-count">{notifications.length}</output>
      {notifications.map((notification) => (
        <article
          key={notification.id}
          data-testid={`notification-${notification.id}`}
          data-read={String(notification.read)}
          data-type={notification.type}
        >
          <h2>{notification.title}</h2>
          <p>{notification.message}</p>
          <time>{notification.time}</time>
        </article>
      ))}
      <button onClick={() => void markNotificationsRead()}>Mark notifications read</button>
    </>
  );
}

describe("cloud delivery requester identity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getUser.mockResolvedValue({
      data: { user: { id: "user-123", email: "auth-metadata@miit.edu.mm" } },
      error: null,
    });
    mocks.profileRoleSingle.mockResolvedValue({ data: { role: "USER" }, error: null });
    mocks.requesterProfileMaybeSingle.mockResolvedValue({
      data: { full_name: "  Profile Person  ", email: "  profile@miit.edu.mm  " },
      error: null,
    });
    mocks.deliveryRowsOrder.mockResolvedValue({ data: [], error: null });
    mocks.robotRowsOrder.mockResolvedValue({ data: [], error: null });
    mocks.notificationRowsLimit.mockResolvedValue({ data: [], error: null });
    mocks.rpc.mockResolvedValue({ data: null, error: null });
    mocks.deliveryInsertSingle.mockResolvedValue({
      data: { id: "delivery-123", tracking_code: "MIIT-2001" },
      error: null,
    });
    mocks.deliveryInsert.mockImplementation(() => ({
      select: () => ({ single: mocks.deliveryInsertSingle }),
    }));
  });

  it("uses the authenticated profile snapshot instead of demo or auth metadata labels", async () => {
    const user = userEvent.setup();
    render(<AppProvider><CreateDeliveryHarness /></AppProvider>);

    await user.click(screen.getByRole("button", { name: "Create cloud delivery" }));
    expect(await screen.findByText("created")).toBeTruthy();

    await waitFor(() => expect(mocks.deliveryInsert).toHaveBeenCalledTimes(1));
    expect(mocks.deliveryInsert.mock.calls[0][0]).toMatchObject({
      requester_id: "user-123",
      requester_name: "Profile Person",
      requester_email: "profile@miit.edu.mm",
    });
  });

  it("does not insert a delivery when the authenticated profile is missing", async () => {
    mocks.requesterProfileMaybeSingle.mockResolvedValueOnce({ data: null, error: null });
    const user = userEvent.setup();
    render(<AppProvider><CreateDeliveryHarness /></AppProvider>);

    await user.click(screen.getByRole("button", { name: "Create cloud delivery" }));

    expect(await screen.findByText(/account profile is missing/i)).toBeTruthy();
    expect(mocks.deliveryInsert).not.toHaveBeenCalled();
  });
});

describe("cloud database notifications", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getUser.mockResolvedValue({
      data: { user: { id: "user-123", email: "user@miit.edu.mm" } },
      error: null,
    });
    mocks.profileRoleSingle.mockResolvedValue({ data: { role: "USER" }, error: null });
    mocks.deliveryRowsOrder.mockResolvedValue({ data: [], error: null });
    mocks.robotRowsOrder.mockResolvedValue({ data: [], error: null });
    mocks.notificationRowsLimit.mockResolvedValue({ data: [], error: null });
    mocks.rpc.mockResolvedValue({ data: null, error: null });
  });

  it("queries and maps notification rows from Supabase", async () => {
    mocks.notificationRowsLimit.mockResolvedValueOnce({
      data: [{
        id: "notification-db-1",
        title: "Delivery approved",
        message: "MIIT-2001 is ready for assignment.",
        type: "success",
        created_at: "2026-07-20T08:30:00.000Z",
        read_at: null,
      }],
      error: null,
    });

    render(<AppProvider><NotificationsHarness /></AppProvider>);

    expect(await screen.findByText("Delivery approved")).toBeTruthy();
    expect(screen.getByText("MIIT-2001 is ready for assignment.")).toBeTruthy();
    expect(screen.getByText("2026-07-20T08:30:00.000Z")).toBeTruthy();
    const notification = screen.getByTestId("notification-notification-db-1");
    expect(notification.getAttribute("data-type")).toBe("success");
    expect(notification.getAttribute("data-read")).toBe("false");
    expect(mocks.notificationSelect).toHaveBeenCalledWith("id, title, message, type, created_at, read_at");
    expect(mocks.notificationRowsOrder).toHaveBeenCalledWith("created_at", { ascending: false });
    expect(mocks.notificationRowsLimit).toHaveBeenCalledWith(50);
  });

  it("does not leak demo notifications into cloud mode", async () => {
    render(<AppProvider><NotificationsHarness /></AppProvider>);

    await waitFor(() => expect(mocks.notificationRowsLimit).toHaveBeenCalled());
    expect(screen.getByTestId("notification-count").textContent).toBe("0");
    expect(screen.queryByText("Rover 01 reached Library junction")).toBeNull();
    expect(screen.queryByText("Delivery request received")).toBeNull();
    expect(screen.queryByText("Delivery completed")).toBeNull();
  });

  it("registers notifications with the realtime refresh channel", async () => {
    render(<AppProvider><NotificationsHarness /></AppProvider>);

    await waitFor(() => {
      expect(mocks.channelOn).toHaveBeenCalledWith(
        "postgres_changes",
        { event: "*", schema: "public", table: "notifications" },
        expect.any(Function),
      );
    });
  });

  it("marks unread notifications locally and through the database RPC", async () => {
    mocks.notificationRowsLimit.mockResolvedValueOnce({
      data: [{
        id: "notification-db-2",
        title: "Robot warning",
        message: "Robot 01 reported a low battery.",
        type: "warning",
        created_at: "2026-07-20T08:35:00.000Z",
        read_at: null,
      }],
      error: null,
    });
    const user = userEvent.setup();
    render(<AppProvider><NotificationsHarness /></AppProvider>);

    const notification = await screen.findByTestId("notification-notification-db-2");
    expect(notification.getAttribute("data-read")).toBe("false");

    await user.click(screen.getByRole("button", { name: "Mark notifications read" }));

    await waitFor(() => expect(mocks.rpc).toHaveBeenCalledWith("mark_notifications_read"));
    expect(notification.getAttribute("data-read")).toBe("true");
  });
});
