# V1 Phase II Requirements Analysis

Source document: `Documentos/Requirement-2nd.docx`

This document summarizes the Phase II direction for Team V1 - AI and Motion Recognition. The source file is a Word document even though it was described as a requirement PDF in the task.

## 1. General Phase II Direction

Phase II changes the V1 work from a broad AI-first pipeline into a practical standard-curve workflow.

The previous V1 direction was based on IMU processing and future AI analysis. That direction is still useful as a long-term goal, but the requirement meeting makes clear that the current system does not yet have enough clean, organized, labeled motion data for reliable AI recommendations. Because of that, Phase II should first solve the data and workflow foundation.

The current focus is:

- collect healthy-person motion curves for standard rehabilitation actions;
- fit standard reference curves from those healthy curves;
- display standard curves together with patient curves;
- compare patient curves against standard curves using measurable differences;
- help doctors review patient motion status more intuitively;
- postpone automatic AI recommendations until enough valid data exists.

This is a practical and immediate direction because doctors can already benefit from standard-vs-patient curve comparison even before a full AI model exists. The comparison can show differences in amplitude, rhythm, timing, and curve shape. These outputs can support doctor review without claiming automatic diagnosis.

Full AI recommendations are postponed because the requirement document explicitly says the current data volume is insufficient. A model trained on too little or inconsistent data could produce unreliable suggestions. Phase II should therefore accumulate standardized data first, then gradually add rule-based or AI-assisted analysis later.

## 2. Current System Issues Mentioned in the Requirement Document

### Missing doctor-patient binding

The doctor-side system can currently view all patient data. This is a serious workflow and privacy problem. The requirement says patients should register only through a doctor-provided link or QR code. After registration, the patient should be automatically bound to that doctor, and doctors should only view their own bound patients.

This affects V1 indirectly because patient motion data must later be compared against standard curves in a doctor-specific patient workflow.

### Disorganized motion curves

The collected motion curves are not yet organized into a clear action-based structure. The system has motion data, but it lacks a reliable standard format for saying which session belongs to which action, which angle should be analyzed, and whether the data is healthy baseline or patient data.

For V1, this means standard-curve building must record metadata such as action name, angle ID, source sessions, ignored sessions, output version, and quality statistics.

### Lack of standard reference curves

The current system lacks standard reference curves. Without these curves, patient curves can be displayed, but doctors do not have a healthy baseline for comparison.

V1's immediate responsibility is to build these reference curves for standard rehabilitation actions, starting from available healthy-person data.

### AI recommendation not implemented

The requirement document says the AI recommendation function has not been implemented. It also says AI recommendations should be postponed because there is not enough data.

For V1, this means the current deliverable should not be a deep-learning recommendation model. The deliverable should be a standard-curve pipeline and comparison metrics that can later support AI or rule-based recommendations.

### Insufficient data volume

The system does not yet have enough valid data for robust AI. More healthy participant data and patient data must be collected before AI can make meaningful automatic suggestions.

This affects V1 because standard curves built from very small datasets should be marked as preliminary. For example, squat currently has only sessions `116` and `118`, so the curve can be useful for pipeline testing but should not be treated as a final clinical baseline.

### Bluetooth/device compatibility issues

Sensor Bluetooth compatibility varies across mobile phones. The requirement recommends using a unified doctor-side device, such as a tablet, for acquisition.

For V1, this matters because inconsistent devices can create inconsistent data quality. A stable acquisition device makes standard curves more reliable and reduces noise caused by hardware or phone compatibility differences.

## 3. Core Requirements From Dr. Yin

### Standard rehabilitation actions

Dr. Yin requires standard actions for rehabilitation evaluation. The meeting specifically lists:

- Squat
- Walking
- Stair climbing

These actions should be designed as repeatable acquisition tasks so healthy-person data and patient data can be compared in the same format.

### Healthy-person baseline data

Healthy-person data should be collected as baseline data. V1 should fit standard curves from that baseline and provide the curves to the rest of the system for comparison against patient curves.

### Doctor-patient binding

Patients should not register independently. They should register through a doctor-provided link or QR code. The system should automatically bind the patient to the doctor who provided the registration path.

This is mainly an M1, M2, and V2 requirement, but V1 comparison outputs should be designed so they can be associated with a patient, doctor, session, action, and standard-curve version.

### Unified acquisition device

Sensors should be bound to a single doctor-side device to reduce Bluetooth compatibility issues. This should improve data quality and make standard curves more stable.

### Postponed AI recommendations

AI recommendations should be postponed until the system has enough valid data. Phase II should not over-focus on advanced AI before data quality, action definitions, and workflow structure are stable.

### Possible doctor assistant AI

The document also mentions a possible WeChat-based doctor assistant AI for common patient questions, such as clinic hours, appointments, and process explanations. This is separate from motion-curve AI and depends on WeChat platform restrictions. It is not the immediate V1 standard-curve task.

## 4. Required Standard Actions

### Squat

Patient task: complete 3 consecutive squat repetitions in about 5 seconds.

Expected curve: the knee angle should change strongly during each repetition. Depending on how the sensor calculates the angle, each squat may appear as a rise from standing to bending and then a fall back to standing, or the inverse. The important feature is a repeated large-amplitude movement pattern.

What V1 probably needs to extract:

- repetition boundaries;
- peak or minimum knee angle per repetition;
- range of motion;
- repetition duration;
- consistency across the 3 repetitions;
- average normalized squat repetition curve;
- deviation from healthy standard curve.

Current known data: sessions `116` and `118` are squat data. Sessions `117` and `119` must be ignored.

### Walking

Patient task: walk forward for 5 meters.

Expected curve: the knee angle should form repeated periodic cycles. The current `Walk.py` implementation treats one walking cycle as the segment between two neighboring local minima in the `left_knee` angle curve.

What V1 probably needs to extract:

- walking cycles;
- cycle duration;
- cadence or cycles per minute;
- peak knee angle;
- amplitude;
- normalized average walking cycle;
- standard deviation band across healthy cycles;
- patient-vs-standard deviation.

Current known data: healthy walking sessions are `91-115`. Processed walking user/patient sessions are `33-57` and may be useful later for patient-vs-standard comparison.

### Stair Climbing

Patient task: climb 10 steps.

Expected curve: the knee angle may show repeated step-like movement, but it may be more complex than walking because stair climbing involves stronger knee flexion, weight transfer, and possible left/right asymmetry.

What V1 probably needs to extract:

- step or stair-cycle boundaries;
- peak knee angle for each step;
- duration per step;
- rhythm consistency;
- possible fatigue or asymmetry indicators;
- standard stair-climbing curve once data is available.

Current known data: stair climbing session IDs are unknown and not provided yet. V1 must not invent these IDs. Stair climbing should be marked as pending data.

## 5. V1 Responsibilities in Phase II

V1 is responsible for the AI and motion-recognition part of Phase II, but the immediate implementation should focus on standard curves and comparison outputs.

V1 should receive healthy-person curves from S1. These curves should be action-specific and should include enough metadata to know which action was performed, which angle IDs are available, and which sessions are valid.

V1 should fit standard curves from healthy-person data. A standard curve should include at least the normalized time percent, fitted angle, mean angle, standard deviation, and source session metadata.

V1 should compare patient curves with standard curves. This comparison can measure differences in peak angle, minimum angle, amplitude, duration, cadence, curve shape, and percentage outside the healthy standard deviation band.

V1 should extract differences in a form that doctors can understand. The goal is not to make a medical diagnosis, but to highlight measurable differences that may help doctors review patient recovery status.

V1 should help doctors analyze patient status by producing clear curves, CSV data, and later structured JSON that M2 can display.

V1 should prepare future automatic analysis and recommendations. This means storing metrics, curve versions, and quality information now, so future rule-based or AI-assisted recommendations can be built on consistent data.

## 6. Requirements That Affect M1, M2, S1, S2, and V2

### S1 - Sensor Group

S1 provides healthy participant data. This is the direct input to V1 standard-curve fitting.

S1 also defines the acquisition workflow:

- squat: 3 consecutive repetitions in about 5 seconds;
- walking: walk forward for 5 meters;
- stair climbing: climb 10 steps.

S1 must improve data acquisition quality so curves are smoother and more regular.

### S2 - Data Group

S2 supports storage, processing, and export of standard curves and patient curves. V1 needs S2 to provide clean session exports or API access.

S2 also supports visualization improvements such as zooming, dragging to select time ranges, and entering detail pages from chart session records. These features can help V1 visually validate segmentation and curve fitting.

### V2 - Database and Backend Group

V2 stores and queries standard curves and patient curves. V2 also implements doctor-patient binding logic and ensures doctors can only query their own patients.

For V1, V2 should eventually store:

- standard curve points;
- action name;
- angle ID;
- source healthy sessions;
- curve version;
- date generated;
- average duration;
- quality statistics.

### M2 - Web End for Doctors

M2 displays standard-vs-patient comparison. It should overlay standard curves and patient curves and show comparison analysis for amplitude, frequency, width, and other curve features.

V1 should provide outputs in a format M2 can display, such as CSV during development and JSON/API payloads later.

### M1 - Mobile End for Patients

M1 handles patient registration, binding, prescription display, action teaching videos, and completion marking. Sensors are not required on the patient side by default.

This affects V1 because patient-side action completion and measurement sessions must later map to the same action names used by the standard-curve pipeline.

## 7. Implementation Priority

The immediate priority is standard curves, stable data, and workflow foundation.

Standard curves are needed because they give doctors a baseline for comparison. Without a baseline, the system can show patient curves but cannot explain whether a patient curve is close to or far from healthy movement patterns.

Stable data is needed because bad acquisition data will produce bad standard curves and unreliable comparisons. A unified acquisition device and clean session labeling are therefore more important than advanced AI in the current stage.

Workflow foundation is needed because the system must know which doctor owns which patient, which session belongs to which action, and which curve version is being compared.

Advanced AI should wait because the data volume is currently insufficient. In Phase II, V1 can still design simple rule-based, AI-assisted language for later, but should not present it as a medical diagnosis.

## 8. Final Conclusions for V1

V1 should implement now:

- walking standard-curve documentation and validation using sessions `91-115`;
- squat standard-curve pipeline design using sessions `116` and `118`;
- clear ignored-session handling for sessions `117` and `119`;
- standard output formats for curve CSV, summaries, and visual HTML;
- patient-vs-standard comparison design;
- cautious rule-based recommendation design for future use.

V1 should not implement yet:

- full deep-learning recommendation models;
- clinical diagnosis logic;
- stair-climbing standard curves without real session IDs;
- database write-back until V2 confirms the storage/API format;
- final medical recommendation text without doctor validation.

Information still missing:

- stair-climbing session IDs;
- final standard action metadata format from S1/S2/V2;
- expected database schema or API shape from V2;
- M2 display payload format;
- visual validation of squat segmentation;
- more healthy participant data, especially for squat and stair climbing.

