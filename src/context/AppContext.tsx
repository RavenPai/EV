import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { initialDeliveries, initialNotifications, initialRobots } from "../data/demo";
import { createId } from "../lib/id";
import { cloudEnabled, supabase } from "../lib/supabase";
import type { Delivery, DeliveryStatus, NewDeliveryInput, NotificationItem, Robot, UserRole } from "../types";

type Toast = { id: string; message: string; tone: "success" | "warning" | "danger" };

interface AppContextValue {
  role: UserRole;
  setRole: (role: UserRole) => void;
  deliveries: Delivery[];
  robots: Robot[];
  notifications: NotificationItem[];
  toast?: Toast;
  dismissToast: () => void;
  createDelivery: (input: NewDeliveryInput) => Promise<string>;
  approveDelivery: (id: string) => Promise<void>;
  assignDelivery: (id: string, robotId: string) => Promise<void>;
  dispatchDelivery: (id: string) => Promise<void>;
  cancelDelivery: (id: string) => Promise<void>;
  advanceDelivery: (id: string) => Promise<void>;
  sendRobotCommand: (robotId: string, command: "PAUSE" | "RESUME" | "RETURN_HOME" | "ESTOP") => Promise<void>;
  markNotificationsRead: () => void;
  resetDemo: () => void;
}

const AppContext = createContext<AppContextValue | null>(null);
const DELIVERY_KEY = "miit-rover-deliveries-v1";
const ROBOT_KEY = "miit-rover-robots-v1";

const readLocal = <T,>(key: string, fallback: T): T => {
  try {
    const saved = localStorage.getItem(key);
    return saved ? (JSON.parse(saved) as T) : fallback;
  } catch {
    return fallback;
  }
};

const mapCloudDelivery = (row: Record<string, unknown>): Delivery => ({
  id: String(row.id),
  trackingCode: String(row.tracking_code),
  requesterName: String(row.requester_name ?? "Campus user"),
  requesterEmail: String(row.requester_email ?? ""),
  recipientName: String(row.recipient_name),
  recipientPhone: String(row.recipient_phone ?? ""),
  sourceId: String(row.source_id),
  destinationId: String(row.destination_id),
  itemName: String(row.item_name),
  category: String(row.category),
  weightKg: Number(row.weight_kg),
  priority: row.priority as Delivery["priority"],
  status: row.status as DeliveryStatus,
  robotId: row.robot_id ? String(row.robot_id) : undefined,
  createdAt: String(row.created_at),
  updatedAt: String(row.updated_at),
  etaMinutes: row.eta_minutes == null ? undefined : Number(row.eta_minutes),
  notes: row.notes ? String(row.notes) : undefined,
  unlockCode: row.unlock_code ? String(row.unlock_code) : undefined,
  progress: Number(row.progress ?? 0),
});

const mapCloudRobot = (row: Record<string, unknown>): Robot => ({
  id: String(row.id), name: String(row.name), model: String(row.model),
  status: row.status as Robot["status"], mode: row.mode as Robot["mode"],
  battery: Number(row.battery), locationId: String(row.location_id), signal: Number(row.signal),
  lastSeen: String(row.last_seen), currentDeliveryId: row.current_delivery_id ? String(row.current_delivery_id) : undefined,
  speedMps: Number(row.speed_mps), lidar: row.lidar as Robot["lidar"], camera: row.camera as Robot["camera"],
  esp32: row.esp32 as Robot["esp32"], motorTempC: Number(row.motor_temp_c),
});

export function AppProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<UserRole>("ADMIN");
  const [deliveries, setDeliveries] = useState<Delivery[]>(() => readLocal(DELIVERY_KEY, initialDeliveries));
  const [robots, setRobots] = useState<Robot[]>(() => readLocal(ROBOT_KEY, initialRobots));
  const [notifications, setNotifications] = useState(initialNotifications);
  const [toast, setToast] = useState<Toast>();

  const showToast = useCallback((message: string, tone: Toast["tone"] = "success") => {
    setToast({ id: createId(), message, tone });
  }, []);

  const refreshCloud = useCallback(async () => {
    if (!cloudEnabled || !supabase) return;
    const { data: authData } = await supabase.auth.getUser();
    if (authData.user) {
      const { data: profile } = await supabase.from("profiles").select("role").eq("id", authData.user.id).single();
      if (profile?.role) setRole(profile.role as UserRole);
    }
    const [{ data: deliveryRows, error: deliveryError }, { data: robotRows, error: robotError }] = await Promise.all([
      supabase.from("deliveries").select("*").order("created_at", { ascending: false }),
      supabase.from("robots").select("*").order("name"),
    ]);
    if (deliveryError || robotError) {
      showToast("Cloud data could not be loaded. Check RLS and environment settings.", "warning");
      return;
    }
    if (deliveryRows) setDeliveries(deliveryRows.map((row) => mapCloudDelivery(row)));
    if (robotRows) setRobots(robotRows.map((row) => mapCloudRobot(row)));
  }, [showToast]);

  useEffect(() => {
    if (!cloudEnabled || !supabase) return;
    const client = supabase;
    void refreshCloud();
    const channel = client
      .channel("operations-live")
      .on("postgres_changes", { event: "*", schema: "public", table: "deliveries" }, refreshCloud)
      .on("postgres_changes", { event: "*", schema: "public", table: "robots" }, refreshCloud)
      .subscribe();
    return () => { void client.removeChannel(channel); };
  }, [refreshCloud]);

  useEffect(() => {
    if (!cloudEnabled) localStorage.setItem(DELIVERY_KEY, JSON.stringify(deliveries));
  }, [deliveries]);

  useEffect(() => {
    if (!cloudEnabled) localStorage.setItem(ROBOT_KEY, JSON.stringify(robots));
  }, [robots]);

  const updateDelivery = async (id: string, patch: Partial<Delivery>, successMessage: string) => {
    if (cloudEnabled && supabase) {
      const dbPatch: Record<string, unknown> = {};
      if (patch.status) dbPatch.status = patch.status;
      if (patch.robotId !== undefined) dbPatch.robot_id = patch.robotId;
      if (patch.progress !== undefined) dbPatch.progress = patch.progress;
      if (patch.etaMinutes !== undefined) dbPatch.eta_minutes = patch.etaMinutes;
      dbPatch.updated_at = new Date().toISOString();
      const { error } = await supabase.from("deliveries").update(dbPatch).eq("id", id);
      if (error) throw error;
      await refreshCloud();
    } else {
      setDeliveries((items) => items.map((item) => item.id === id ? { ...item, ...patch, updatedAt: new Date().toISOString() } : item));
    }
    showToast(successMessage);
  };

  const createDelivery = async (input: NewDeliveryInput) => {
    if (cloudEnabled && supabase) {
      const { data: authData, error: authError } = await supabase.auth.getUser();
      if (authError || !authData.user) {
        throw new Error("Your session has expired. Sign in again before creating a delivery.");
      }

      const { data: profile, error: profileError } = await supabase
        .from("profiles")
        .select("full_name, email")
        .eq("id", authData.user.id)
        .maybeSingle();
      if (profileError) {
        throw new Error("Your authenticated profile could not be loaded. Please try again.");
      }
      if (!profile) {
        throw new Error("Your account profile is missing. Contact an administrator before creating a delivery.");
      }

      const requesterName = typeof profile.full_name === "string" ? profile.full_name.trim() : "";
      const requesterEmail = typeof profile.email === "string" ? profile.email.trim() : "";
      if (!requesterName || !requesterEmail) {
        throw new Error("Your profile must include a full name and email before creating a delivery.");
      }

      const { data, error } = await supabase.from("deliveries").insert({
        requester_id: authData.user.id,
        requester_name: requesterName,
        requester_email: requesterEmail,
        recipient_name: input.recipientName,
        recipient_phone: input.recipientPhone,
        source_id: input.sourceId,
        destination_id: input.destinationId,
        item_name: input.itemName,
        category: input.category,
        weight_kg: input.weightKg,
        priority: input.priority,
        notes: input.notes,
      }).select("id, tracking_code").single();
      if (error) throw error;
      await refreshCloud();
      showToast(`${data.tracking_code} was submitted for approval.`);
      return String(data.id);
    }

    const sequence = Math.max(1050, ...deliveries.map((item) => Number(item.trackingCode.split("-")[1]) || 0)) + 1;
    const trackingCode = `MIIT-${sequence}`;
    const now = new Date().toISOString();
    const newDelivery: Delivery = {
      id: createId(), trackingCode, requesterName: "Demo User", requesterEmail: "user@miit.edu.mm",
      ...input, status: "REQUESTED", createdAt: now, updatedAt: now, progress: 5, etaMinutes: 18,
    };
    setDeliveries((items) => [newDelivery, ...items]);
    setNotifications((items) => [{ id: createId(), title: "Delivery request created", message: `${trackingCode} is waiting for approval.`, time: now, read: false, type: "info" }, ...items]);
    showToast(`${trackingCode} was submitted for approval.`);
    return newDelivery.id;
  };

  const approveDelivery = (id: string) => updateDelivery(id, { status: "APPROVED", progress: 12 }, "Delivery request approved.");
  const assignDelivery = async (id: string, robotId: string) => {
    await updateDelivery(id, { status: "ASSIGNED", robotId, progress: 20 }, "Robot assigned to the delivery.");
    if (!cloudEnabled) setRobots((items) => items.map((robot) => robot.id === robotId ? { ...robot, currentDeliveryId: id } : robot));
  };

  const dispatchDelivery = async (id: string) => {
    if (cloudEnabled && supabase) {
      const { error } = await supabase.functions.invoke("dispatch-delivery", { body: { deliveryId: id } });
      if (error) throw error;
      await refreshCloud();
      showToast("Mission command published. Waiting for the robot to start.");
      return;
    }
    const delivery = deliveries.find((item) => item.id === id);
    await updateDelivery(id, { status: "TO_SOURCE", progress: 28, etaMinutes: 12 }, "Mission command acknowledged by the demo robot.");
    if (delivery?.robotId) setRobots((items) => items.map((robot) => robot.id === delivery.robotId ? { ...robot, mode: "AUTO", status: "BUSY", currentDeliveryId: id } : robot));
  };

  const cancelDelivery = (id: string) => updateDelivery(id, { status: "CANCELLED", progress: 0 }, "Delivery cancelled.");

  const advanceDelivery = async (id: string) => {
    const next: Partial<Record<DeliveryStatus, { status: DeliveryStatus; progress: number; eta: number }>> = {
      ASSIGNED: { status: "TO_SOURCE", progress: 28, eta: 12 },
      TO_SOURCE: { status: "AT_SOURCE", progress: 40, eta: 10 },
      AT_SOURCE: { status: "PACKAGE_LOADED", progress: 50, eta: 9 },
      PACKAGE_LOADED: { status: "TO_DESTINATION", progress: 62, eta: 7 },
      TO_DESTINATION: { status: "AT_DESTINATION", progress: 82, eta: 2 },
      AT_DESTINATION: { status: "DELIVERED", progress: 90, eta: 1 },
      DELIVERED: { status: "RETURNING", progress: 95, eta: 5 },
      RETURNING: { status: "COMPLETED", progress: 100, eta: 0 },
    };
    const delivery = deliveries.find((item) => item.id === id);
    const transition = delivery ? next[delivery.status] : undefined;
    if (!transition) return;
    await updateDelivery(id, { status: transition.status, progress: transition.progress, etaMinutes: transition.eta }, `Mission advanced to ${transition.status.toLowerCase().replaceAll("_", " ")}.`);
    if (transition.status === "COMPLETED" && delivery?.robotId) {
      setRobots((items) => items.map((robot) => robot.id === delivery.robotId ? { ...robot, status: "ONLINE", mode: "IDLE", currentDeliveryId: undefined, speedMps: 0 } : robot));
    }
  };

  const sendRobotCommand = async (robotId: string, command: "PAUSE" | "RESUME" | "RETURN_HOME" | "ESTOP") => {
    if (cloudEnabled && supabase) {
      const { error } = await supabase.functions.invoke("dispatch-delivery", { body: { robotId, command } });
      if (error) throw error;
      showToast(`${command.replace("_", " ")} command sent.`);
      return;
    }
    setRobots((items) => items.map((robot) => {
      if (robot.id !== robotId) return robot;
      if (command === "ESTOP") return { ...robot, mode: "ESTOP", status: "FAULT", speedMps: 0 };
      if (command === "PAUSE") return { ...robot, mode: "PAUSED", speedMps: 0 };
      if (command === "RESUME") return { ...robot, mode: robot.currentDeliveryId ? "AUTO" : "IDLE", status: robot.currentDeliveryId ? "BUSY" : "ONLINE" };
      return { ...robot, mode: "AUTO", status: "BUSY", locationId: "loc-home" };
    }));
    showToast(`${command.replace("_", " ")} command acknowledged in demo mode.`, command === "ESTOP" ? "danger" : "success");
  };

  const resetDemo = () => {
    setDeliveries(initialDeliveries);
    setRobots(initialRobots);
    setNotifications(initialNotifications);
    localStorage.removeItem(DELIVERY_KEY);
    localStorage.removeItem(ROBOT_KEY);
    showToast("Demo data restored.");
  };

  const value = useMemo<AppContextValue>(() => ({
    role, setRole, deliveries, robots, notifications, toast, dismissToast: () => setToast(undefined),
    createDelivery, approveDelivery, assignDelivery, dispatchDelivery, cancelDelivery, advanceDelivery,
    sendRobotCommand, markNotificationsRead: () => setNotifications((items) => items.map((item) => ({ ...item, read: true }))), resetDemo,
  }), [role, deliveries, robots, notifications, toast]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const context = useContext(AppContext);
  if (!context) throw new Error("useApp must be used inside AppProvider");
  return context;
}
