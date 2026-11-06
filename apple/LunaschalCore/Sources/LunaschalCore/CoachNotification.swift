import Foundation

// The coach's push notifications (backend/push/, backend/coach/deliver.py):
// what a payload says, which buttons each kind carries, and what a tap or a
// typed reply does. Pure, so it builds on Linux and is tested without
// UserNotifications; the app registers these identifiers and hands
// `CoachRoute.resolve` whatever the user did.

/// `aps.category`. Each one is a set of buttons registered at launch.
public enum CoachCategory: String, CaseIterable {
    case habit = "COACH_HABIT"
    case weight = "COACH_WEIGHT"
    case calories = "COACH_CALORIES"
    case checkin = "COACH_CHECKIN"
    case goals = "COACH_GOALS"
    case test = "COACH_TEST"

    /// The buttons, in the order the lock screen shows them.
    public var actions: [CoachAction] {
        switch self {
        case .habit: return [.done, .snooze]
        case .weight: return [.logWeight, .snooze]
        case .calories: return [.logCalories, .snooze]
        case .checkin: return [.reply]
        case .goals, .test: return []
        }
    }
}

public enum CoachAction: String, CaseIterable {
    case done = "COACH_DONE"
    case snooze = "COACH_SNOOZE"
    case logWeight = "COACH_LOG_WEIGHT"
    case logCalories = "COACH_LOG_CALORIES"
    case reply = "COACH_REPLY"

    public var title: String {
        switch self {
        case .done: return "Done"
        case .snooze: return "Later"
        case .logWeight: return "Log weight"
        case .logCalories: return "Log calories"
        case .reply: return "Reply"
        }
    }

    /// Typed on the lock screen rather than tapped.
    public var takesText: Bool { [.logWeight, .logCalories, .reply].contains(self) }

    public var placeholder: String? {
        switch self {
        case .logWeight: return "kg, e.g. 81.4"
        case .logCalories: return "e.g. Pasta 650"
        case .reply: return "How did it go?"
        default: return nil
        }
    }
}

/// The payload's `coach` dictionary.
public struct CoachPayload: Equatable {
    public var kind: String
    public var day: String?
    public var messageID: String?
    public var conversationID: String?
    /// The habit a habit reminder is about.
    public var habitID: String?
    /// The decision a reminder came from, for reporting what was done with it.
    public var decisionID: String?
    public var horizons: [GoalHorizon]

    public init(kind: String, day: String? = nil, messageID: String? = nil, conversationID: String? = nil,
                habitID: String? = nil, decisionID: String? = nil, horizons: [GoalHorizon] = []) {
        self.kind = kind; self.day = day; self.messageID = messageID; self.conversationID = conversationID
        self.habitID = habitID; self.decisionID = decisionID; self.horizons = horizons
    }

    /// From `UNNotificationContent.userInfo`; nil for anything that isn't the coach's.
    public init?(userInfo: [AnyHashable: Any]) {
        guard let coach = userInfo["coach"] as? [String: Any], let kind = coach["kind"] as? String else { return nil }
        self.init(kind: kind, day: coach["day"] as? String, messageID: coach["messageId"] as? String,
                  conversationID: coach["conversationId"] as? String, habitID: coach["habitId"] as? String,
                  decisionID: coach["decisionId"] as? String,
                  horizons: (coach["horizons"] as? [String] ?? []).compactMap(GoalHorizon.init(rawValue:)))
    }
}

extension CoachRoute {
    /// What to tell the server the user did, for a reminder the engine sent.
    public var response: String {
        switch self {
        case .snooze: return "snooze"
        case .tickHabit, .logWeight, .logCalories: return "done"
        default: return "opened"
        }
    }
}

/// What the app does with a notification response.
public enum CoachRoute: Equatable {
    /// Opened: the chat holds the message the notification came from.
    case openChat
    case openToday
    case openDaily
    /// Done, for the day the reminder was about — not today, if it's past 4am.
    case tickHabit(id: String, day: String)
    case logWeight(kg: Double)
    case logCalories(CalorieLine)
    case reply(String)
    case snooze
    /// A typed value that couldn't be read: open the screen where it can be fixed.
    case retype(CoachAction)

    /// The tap or the typed text, read into one thing to do. `today` is the
    /// current 4am day, used when a payload carries no day of its own.
    public static func resolve(_ payload: CoachPayload, action: String, text: String? = nil,
                               today: String) -> CoachRoute {
        let typed = (text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        switch CoachAction(rawValue: action) {
        case .done:
            guard let id = payload.habitID else { return .openToday }
            return .tickHabit(id: id, day: payload.day ?? today)
        case .snooze:
            return .snooze
        case .logWeight:
            guard let kg = Double(typed.replacingOccurrences(of: ",", with: ".")
                .replacingOccurrences(of: "kg", with: "").trimmingCharacters(in: .whitespaces)),
                  kg.isFinite, (1...1000).contains(kg) else { return .retype(.logWeight) }
            return .logWeight(kg: kg)
        case .logCalories:
            guard let line = CalorieLine.parse(typed) else { return .retype(.logCalories) }
            return .logCalories(line)
        case .reply:
            return typed.isEmpty ? .openChat : .reply(typed)
        case nil:
            // The notification itself was tapped.
            switch payload.kind {
            case "habit", "rollover", "goals": return .openToday
            case "weight", "calories": return .openDaily
            default: return .openChat
            }
        }
    }
}
