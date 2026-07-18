import type { CampusLocation, Delivery, NotificationItem, Robot } from "../types";

export const locations: CampusLocation[] = [
  { id: "loc-home", code: "HOME", name: "Robot Station", shortName: "Station", description: "Charging and maintenance bay", x: 15, y: 76, markerId: 10, type: "home" },
  { id: "loc-fcs", code: "FCS", name: "Faculty of Computer Science", shortName: "FCS", description: "Main computer science building", x: 29, y: 30, markerId: 20, type: "academic" },
  { id: "loc-fcst", code: "FCST", name: "Faculty of Computer Systems & Technologies", shortName: "FCST", description: "Systems and technology building", x: 49, y: 18, markerId: 21, type: "academic" },
  { id: "loc-library", code: "LIB", name: "MIIT Library", shortName: "Library", description: "Central library entrance", x: 67, y: 34, markerId: 24, type: "service" },
  { id: "loc-data", code: "DC", name: "Data Center", shortName: "Data Center", description: "Campus data center reception", x: 80, y: 62, markerId: 30, type: "service" },
  { id: "loc-rector", code: "RECTOR", name: "Rector Office", shortName: "Rector", description: "Administration building", x: 54, y: 72, markerId: 40, type: "administration" },
  { id: "loc-canteen", code: "CANTEEN", name: "Campus Canteen", shortName: "Canteen", description: "Main canteen pickup point", x: 30, y: 58, markerId: 45, type: "service" },
];

const now = new Date();
const minutesAgo = (minutes: number) => new Date(now.getTime() - minutes * 60_000).toISOString();

export const initialDeliveries: Delivery[] = [
  {
    id: "del-1048", trackingCode: "MIIT-1048", requesterName: "Aye Chan", requesterEmail: "ayechan@miit.edu.mm",
    recipientName: "Dr. Thida", recipientPhone: "+95 9 421 555 018", sourceId: "loc-fcs", destinationId: "loc-data",
    itemName: "Network equipment", category: "Electronics", weightKg: 3.4, priority: "HIGH", status: "TO_DESTINATION",
    robotId: "robot-01", createdAt: minutesAgo(46), updatedAt: minutesAgo(2), etaMinutes: 6, notes: "Handle with care",
    unlockCode: "5821", progress: 68,
  },
  {
    id: "del-1049", trackingCode: "MIIT-1049", requesterName: "Min Khant", requesterEmail: "minkhant@miit.edu.mm",
    recipientName: "Admin Office", recipientPhone: "+95 9 420 300 117", sourceId: "loc-library", destinationId: "loc-rector",
    itemName: "Signed documents", category: "Documents", weightKg: 0.6, priority: "NORMAL", status: "ASSIGNED",
    robotId: "robot-02", createdAt: minutesAgo(31), updatedAt: minutesAgo(8), etaMinutes: 14, progress: 20,
  },
  {
    id: "del-1050", trackingCode: "MIIT-1050", requesterName: "Su Myat", requesterEmail: "sumyat@miit.edu.mm",
    recipientName: "Lab 304", recipientPhone: "+95 9 781 203 220", sourceId: "loc-canteen", destinationId: "loc-fcst",
    itemName: "Lunch packages", category: "Food", weightKg: 4.2, priority: "NORMAL", status: "REQUESTED",
    createdAt: minutesAgo(14), updatedAt: minutesAgo(14), etaMinutes: 18, notes: "Deliver before 12:30 PM", progress: 5,
  },
  {
    id: "del-1046", trackingCode: "MIIT-1046", requesterName: "Nyein Chan", requesterEmail: "nyeinchan@miit.edu.mm",
    recipientName: "Library Desk", recipientPhone: "+95 9 790 121 018", sourceId: "loc-rector", destinationId: "loc-library",
    itemName: "Reference books", category: "Books", weightKg: 5.1, priority: "NORMAL", status: "COMPLETED",
    robotId: "robot-01", createdAt: minutesAgo(190), updatedAt: minutesAgo(96), etaMinutes: 0, progress: 100,
  },
  {
    id: "del-1047", trackingCode: "MIIT-1047", requesterName: "Hnin Pwint", requesterEmail: "hninpwint@miit.edu.mm",
    recipientName: "FCS Office", recipientPhone: "+95 9 444 181 222", sourceId: "loc-data", destinationId: "loc-fcs",
    itemName: "Replacement keyboard", category: "Electronics", weightKg: 1.0, priority: "NORMAL", status: "COMPLETED",
    robotId: "robot-02", createdAt: minutesAgo(280), updatedAt: minutesAgo(211), etaMinutes: 0, progress: 100,
  },
];

export const initialRobots: Robot[] = [
  { id: "robot-01", name: "Rover 01", model: "MIIT EV Mk-II", status: "BUSY", mode: "AUTO", battery: 78, locationId: "loc-library", signal: 92, lastSeen: minutesAgo(0), currentDeliveryId: "del-1048", speedMps: 0.34, lidar: "OK", camera: "OK", esp32: "OK", motorTempC: 42 },
  { id: "robot-02", name: "Rover 02", model: "MIIT EV Mk-II", status: "ONLINE", mode: "IDLE", battery: 91, locationId: "loc-home", signal: 87, lastSeen: minutesAgo(0), currentDeliveryId: "del-1049", speedMps: 0, lidar: "OK", camera: "OK", esp32: "OK", motorTempC: 35 },
  { id: "robot-03", name: "Rover 03", model: "MIIT EV Mk-I", status: "CHARGING", mode: "IDLE", battery: 43, locationId: "loc-home", signal: 73, lastSeen: minutesAgo(1), speedMps: 0, lidar: "WARNING", camera: "OK", esp32: "OK", motorTempC: 31 },
];

export const initialNotifications: NotificationItem[] = [
  { id: "n1", title: "Rover 01 reached Library junction", message: "Mission MIIT-1048 is moving to the Data Center.", time: minutesAgo(2), read: false, type: "info" },
  { id: "n2", title: "Delivery request received", message: "MIIT-1050 is waiting for administrator approval.", time: minutesAgo(14), read: false, type: "warning" },
  { id: "n3", title: "Delivery completed", message: "MIIT-1046 was completed and the cargo lock was secured.", time: minutesAgo(96), read: true, type: "success" },
];

export const statusOrder = [
  "REQUESTED", "APPROVED", "ASSIGNED", "DISPATCHED", "TO_SOURCE", "AT_SOURCE", "PACKAGE_LOADED",
  "TO_DESTINATION", "AT_DESTINATION", "DELIVERED", "RETURNING", "COMPLETED",
] as const;

export const formatStatus = (value: string) =>
  value.toLowerCase().split("_").map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");

export const getLocation = (id: string) => locations.find((location) => location.id === id);
