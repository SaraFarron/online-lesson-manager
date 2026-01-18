# Calendar App - Backend API Specification

This document describes all backend API endpoints required for the Calendar App.

## Base URL

```
https://api.example.com/v1
```

## Authentication

All endpoints (except `/auth/login`) require a Bearer token in the Authorization header:

```
Authorization: Bearer <access_token>
```

---

## Endpoints

### Authentication

#### POST `/auth/login`

Authenticate user and receive access token.

**Request:**
```json
{
  "token": "string (4-16 characters)"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "expiresIn": 86400,
    "user": {
      "id": "user_123",
      "name": "John Doe"
    }
  }
}
```

**Response (401 Unauthorized):**
```json
{
  "success": false,
  "error": {
    "code": "INVALID_TOKEN",
    "message": "Invalid or expired token"
  }
}
```

#### POST `/auth/logout`

Invalidate current session.

**Request:** No body required

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Logged out successfully"
}
```

---

### Events

#### GET `/events`

Retrieve all events for the authenticated user.

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `startDate` | string | No | Filter events from this date (YYYY-MM-DD) |
| `endDate` | string | No | Filter events until this date (YYYY-MM-DD) |

**Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "events": [
      {
        "id": "evt_abc123",
        "title": "Lesson",
        "date": "2026-01-20",
        "startTime": "09:00",
        "duration": 60,
        "isRecurring": true,
        "recurringGroupId": "grp_xyz789"
      },
      {
        "id": "evt_def456",
        "title": "Lesson",
        "date": "2026-01-21",
        "startTime": "14:30",
        "duration": 60,
        "isRecurring": false,
        "recurringGroupId": null
      }
    ],
    "total": 2
  }
}
```

#### GET `/events/:id`

Retrieve a single event by ID.

**Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "id": "evt_abc123",
    "title": "Lesson",
    "date": "2026-01-20",
    "startTime": "09:00",
    "duration": 60,
    "isRecurring": true,
    "recurringGroupId": "grp_xyz789"
  }
}
```

**Response (404 Not Found):**
```json
{
  "success": false,
  "error": {
    "code": "EVENT_NOT_FOUND",
    "message": "Event not found"
  }
}
```

#### POST `/events`

Create a new event. If `isRecurring` is true, creates 12 weekly occurrences.

**Request:**
```json
{
  "title": "Lesson",
  "date": "2026-01-20",
  "startTime": "09:00",
  "duration": 60,
  "isRecurring": true
}
```

**Response (201 Created):**
```json
{
  "success": true,
  "data": {
    "events": [
      {
        "id": "evt_abc123",
        "title": "Lesson",
        "date": "2026-01-20",
        "startTime": "09:00",
        "duration": 60,
        "isRecurring": true,
        "recurringGroupId": "grp_xyz789"
      },
      {
        "id": "evt_abc124",
        "title": "Lesson",
        "date": "2026-01-27",
        "startTime": "09:00",
        "duration": 60,
        "isRecurring": true,
        "recurringGroupId": "grp_xyz789"
      }
      // ... 10 more weekly events
    ],
    "created": 12
  }
}
```

**Response (400 Bad Request) - Validation Error:**
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid event data",
    "details": {
      "title": "Title is required (max 100 characters)",
      "startTime": "Invalid time format (use HH:MM)"
    }
  }
}
```

**Response (409 Conflict) - Overlap:**
```json
{
  "success": false,
  "error": {
    "code": "EVENT_OVERLAP",
    "message": "Event overlaps with an existing event",
    "conflictingEvent": {
      "id": "evt_existing",
      "title": "Existing Lesson",
      "date": "2026-01-20",
      "startTime": "09:30",
      "duration": 60
    }
  }
}
```

#### PUT `/events/:id`

Update an existing event.

**Request:**
```json
{
  "title": "Updated Lesson",
  "date": "2026-01-20",
  "startTime": "10:00",
  "duration": 60
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "id": "evt_abc123",
    "title": "Updated Lesson",
    "date": "2026-01-20",
    "startTime": "10:00",
    "duration": 60,
    "isRecurring": true,
    "recurringGroupId": "grp_xyz789"
  }
}
```

**Response (404 Not Found):**
```json
{
  "success": false,
  "error": {
    "code": "EVENT_NOT_FOUND",
    "message": "Event not found"
  }
}
```

#### DELETE `/events/:id`

Delete a single event.

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Event deleted successfully",
  "deletedId": "evt_abc123"
}
```

#### DELETE `/events/recurring/:recurringGroupId`

Delete all events in a recurring group.

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Recurring events deleted successfully",
  "deletedCount": 12,
  "recurringGroupId": "grp_xyz789"
}
```

---

### Unavailable Slots

External bookings or blocked time slots that prevent scheduling.

#### GET `/unavailable-slots`

Retrieve all unavailable time slots.

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `startDate` | string | No | Filter from this date (YYYY-MM-DD) |
| `endDate` | string | No | Filter until this date (YYYY-MM-DD) |

**Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "slots": [
      {
        "id": "slot_001",
        "date": "2026-01-20",
        "startTime": "12:00",
        "endTime": "13:00",
        "reason": "External booking"
      },
      {
        "id": "slot_002",
        "date": "2026-01-21",
        "startTime": "15:00",
        "endTime": "16:30",
        "reason": "Maintenance"
      }
    ],
    "total": 2
  }
}
```

#### POST `/unavailable-slots`

Create a new unavailable slot.

**Request:**
```json
{
  "date": "2026-01-22",
  "startTime": "14:00",
  "endTime": "15:00",
  "reason": "External booking"
}
```

**Response (201 Created):**
```json
{
  "success": true,
  "data": {
    "id": "slot_003",
    "date": "2026-01-22",
    "startTime": "14:00",
    "endTime": "15:00",
    "reason": "External booking"
  }
}
```

#### DELETE `/unavailable-slots/:id`

Delete an unavailable slot.

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Unavailable slot deleted successfully",
  "deletedId": "slot_003"
}
```

---

### Configuration

#### GET `/config/workday`

Get work day configuration (working hours).

**Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "startHour": 9,
    "endHour": 20,
    "workDays": [1, 2, 3, 4, 5, 6, 7],
    "timezone": "Europe/Moscow"
  }
}
```

#### PUT `/config/workday`

Update work day configuration.

**Request:**
```json
{
  "startHour": 8,
  "endHour": 18,
  "workDays": [1, 2, 3, 4, 5],
  "timezone": "Europe/Moscow"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "startHour": 8,
    "endHour": 18,
    "workDays": [1, 2, 3, 4, 5],
    "timezone": "Europe/Moscow"
  }
}
```

---

## Data Models

### Event

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes (response) | Unique event identifier |
| `title` | string | Yes | Event title (max 100 chars) |
| `date` | string | Yes | Date in YYYY-MM-DD format |
| `startTime` | string | Yes | Start time in HH:MM format (24h) |
| `duration` | number | Yes | Duration in minutes (min: 5, multiple of 5) |
| `isRecurring` | boolean | Yes | Whether event repeats weekly |
| `recurringGroupId` | string | No | Links recurring events together |

### UnavailableSlot

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes (response) | Unique slot identifier |
| `date` | string | Yes | Date in YYYY-MM-DD format |
| `startTime` | string | Yes | Start time in HH:MM format |
| `endTime` | string | Yes | End time in HH:MM format |
| `reason` | string | No | Reason for unavailability |

### WorkDayConfig

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `startHour` | number | Yes | Work day start hour (0-23) |
| `endHour` | number | Yes | Work day end hour (0-23) |
| `workDays` | number[] | Yes | Working days (1=Mon, 7=Sun) |
| `timezone` | string | Yes | IANA timezone string |

---

## Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `INVALID_TOKEN` | 401 | Authentication token is invalid or expired |
| `UNAUTHORIZED` | 401 | User is not authenticated |
| `FORBIDDEN` | 403 | User lacks permission for this action |
| `EVENT_NOT_FOUND` | 404 | Requested event does not exist |
| `SLOT_NOT_FOUND` | 404 | Requested unavailable slot does not exist |
| `VALIDATION_ERROR` | 400 | Request data failed validation |
| `EVENT_OVERLAP` | 409 | Event conflicts with existing event |
| `SLOT_UNAVAILABLE` | 409 | Time slot is blocked by unavailable slot |
| `PAST_DATE` | 400 | Cannot create/modify events in the past |
| `SERVER_ERROR` | 500 | Internal server error |

---

## Rate Limiting

API requests are limited to:
- **100 requests per minute** for authenticated users
- **10 requests per minute** for authentication endpoints

Rate limit headers included in responses:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1737200000
```

**Response (429 Too Many Requests):**
```json
{
  "success": false,
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Too many requests. Please try again later.",
    "retryAfter": 60
  }
}
```

---

## Pagination

For endpoints returning lists, use pagination parameters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | number | 1 | Page number |
| `limit` | number | 50 | Items per page (max: 100) |

**Paginated Response:**
```json
{
  "success": true,
  "data": {
    "events": [...],
    "pagination": {
      "page": 1,
      "limit": 50,
      "total": 150,
      "totalPages": 3,
      "hasNext": true,
      "hasPrev": false
    }
  }
}
```
