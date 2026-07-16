export type UserRole = "USER" | "ADMIN" | "OPERATOR";

export type DeliveryStatus =
  | "REQUESTED"
  | "APPROVED"
  | "ASSIGNED"
  | "TO_SOURCE"
  | "AT_SOURCE"
  | "PACKAGE_LOADED"
  | "TO_DESTINATION"
  | "AT_DESTINATION"
  | "DELIVERED"
  | "RETURNING"
  | "COMPLETED"
  | "PAUSED"
  | "FAILED"
  | "CANCELLED";

export type RobotStatus = "ONLINE" | "BUSY" | "CHARGING" | "OFFLINE" | "FAULT";
export type RobotMode = "IDLE" | "AUTO" | "MANUAL" | "PAUSED" | "ESTOP" | "FAULT";

export interface CampusLocation {
  id: string;
  code: string;
  name: string;
  shortName: string;
  description: string;
  x: number;
  y: number;
  markerId: number;
  type: "academic" | "administration" | "service" | "home";
}

export interface Delivery {
  id: string;
  trackingCode: string;
  requesterName: string;
  requesterEmail: string;
  recipientName: string;
  recipientPhone: string;
  sourceId: string;
  destinationId: string;
  itemName: string;
  category: string;
  weightKg: number;
  priority: "NORMAL" | "HIGH" | "URGENT";
  status: DeliveryStatus;
  robotId?: string;
  createdAt: string;
  updatedAt: string;
  etaMinutes?: number;
  notes?: string;
  unlockCode?: string;
  progress: number;
}

export interface Robot {
  id: string;
  name: string;
  model: string;
  status: RobotStatus;
  mode: RobotMode;
  battery: number;
  locationId: string;
  signal: number;
  lastSeen: string;
  currentDeliveryId?: string;
  speedMps: number;
  lidar: "OK" | "WARNING" | "OFFLINE";
  camera: "OK" | "WARNING" | "OFFLINE";
  esp32: "OK" | "WARNING" | "OFFLINE";
  motorTempC: number;
}

export interface NotificationItem {
  id: string;
  title: string;
  message: string;
  time: string;
  read: boolean;
  type: "info" | "success" | "warning";
}

export interface NewDeliveryInput {
  sourceId: string;
  destinationId: string;
  itemName: string;
  category: string;
  weightKg: number;
  priority: Delivery["priority"];
  recipientName: string;
  recipientPhone: string;
  notes?: string;
}
