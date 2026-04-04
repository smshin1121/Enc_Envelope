# UX/UI Redesign Design Spec

**Date:** 2026-04-03
**Project:** Enc_Envelope (디지털증거 전자봉인시스템)
**Goal:** Production-ready UX/UI with usability, convenience, and visibility

---

## 1. Technology

- **ttkbootstrap** with **cosmo** theme (bright professional, blue accent)
- Preserve existing Tkinter architecture, incremental migration
- Add `ttkbootstrap>=1.10` to requirements.txt

## 2. Theme System

Central theme configuration dict applied globally:

```python
THEME = {
    "name": "cosmo",
    "font_family": "맑은 고딕",
    "font_mono": "Consolas",
    "sizes": {"title": 16, "header": 12, "body": 10, "small": 9},
    "colors": {
        "seal": "#2c3e50",
        "unseal": "#1a5276",
        "reseal": "#1e6e3e",
        "danger": "#e74c3c",
        "success": "#2ecc71",
        "warning": "#f39c12",
        "info": "#3498db",
    },
}
```

## 3. Home Dashboard

### 3.1 Statistics Cards (top row)
- 3 cards: 봉인/봉인해제/재봉인 total counts from DB
- Each card: icon area + count number + label

### 3.2 Quick Launch (middle row)
- 3 large action cards: 봉인/봉인해제/재봉인 + 케이스관리
- Hover effect, clear icon + description

### 3.3 Recent Activity (bottom-left)
- Latest 5 operations with timestamp, type, seal_id
- Click to open case detail

### 3.4 System Status & Alerts (bottom-right)
- DB connection status indicator
- Master key existence check
- Alerts: expiring seals (unlock_time within 3 days), incomplete operations

## 4. Wizard Step Indicator

### 4.1 Component: StepIndicator
- Horizontal bar at top of wizard, below header
- Each step: circle (number) + title label
- States: completed (blue filled ●), active (blue outline with pulse), pending (gray ○)
- Connected by lines between circles
- Responsive: truncate titles on narrow windows

### 4.2 Per-Wizard Steps
- Seal: 7 steps (파일선택, 압수정보, 피압수자, 미리보기, 전자서명, 키분할, 완료)
- Unseal: 5 steps (대상선택, 파일대조, 복호화, 기록지, 완료)
- Reseal: 8 steps (기록로드, 파일비교, 분류, 재봉인정보, 암호화, 기록지, 키분할, 완료)

## 5. Signature Pad Enhancement

### 5.1 Visual Guide
- Placeholder text "여기에 서명하세요" (fades on first stroke)
- Dotted baseline for writing guide
- Clear/redo button with icon

### 5.2 Pressure Simulation
- Line width varies by mouse speed (slow=thick, fast=thin)
- Smooth bezier curves between points

### 5.3 Identity Integration
- Auto-display signer name + date below pad
- SHA-256 hash of signature image data
- Save as PNG (not EPS)
- Embed signature image in PDF seal record
- Record signing duration (start/end timestamps)

### 5.4 Confirmation
- Preview dialog before accepting
- "서명 확인" / "다시 서명" buttons

## 6. Form Field Improvements

### 6.1 Enhanced LabeledEntry
- Inline validation on focus-out (blur)
- Error icon (!) + red text message below field
- Success checkmark on valid input
- Tooltip on hover with input guidance

### 6.2 Field Grouping
- Related fields in ttk.LabelFrame with descriptive title
- S2: "사건 정보" group, "수사관 정보" group
- S3: "피압수자 인적사항" group, "보안 정보" group

### 6.3 Keyboard Navigation
- Explicit tab order across all fields
- Enter key → next step (when all valid)
- Escape → cancel confirmation dialog

## 7. Progress & Feedback

### 7.1 ProgressDialog for All Long Operations
- Seal S1 (encryption) — already done
- Unseal U5 (decryption) — add
- Reseal R5 (re-encryption) — add

### 7.2 Toast Notifications
- Bottom-right popup: success (green), error (red), info (blue)
- Auto-dismiss after 3 seconds
- Stack multiple toasts

### 7.3 Button States
- Disabled buttons: gray + tooltip explaining why
- Loading state: spinner icon during async operations

## 8. Case Manager Improvements

### 8.1 Treeview Enhancement
- Auto-fit column widths
- Status column with colored badges
- Row hover highlight

### 8.2 Context Menu
- Right-click: 상세보기, 산출물, 이력, PDF열기, 삭제

### 8.3 Status Filter
- Dropdown/buttons: 전체/봉인/봉인해제/재봉인

## 9. Responsive Layout
- Window minimum: 900x650 (up from 800x600)
- Widgets use `weight` for proportional resizing
- ScrolledFrame for content that may overflow

## 10. File Structure

```
src/desktop/gui/
├── theme.py              (NEW: theme config + helpers)
├── dashboard.py          (NEW: home dashboard)
├── step_indicator.py     (NEW: wizard step bar)
├── toast.py              (NEW: toast notifications)
├── signature_pad.py      (NEW: enhanced signature, replaces SignaturePad in widgets.py)
├── app.py                (MODIFY: use ttkbootstrap Window, dashboard)
├── seal_wizard.py        (MODIFY: step indicator, themed widgets, form groups)
├── unseal_wizard.py      (MODIFY: step indicator, themed widgets, progress)
├── reseal_wizard.py      (MODIFY: step indicator, themed widgets, progress)
├── case_manager.py       (MODIFY: filters, context menu, themed)
├── case_detail_dialog.py (MODIFY: themed tabs)
├── progress_dialog.py    (MODIFY: themed)
├── widgets.py            (MODIFY: inline validation, tooltips)
└── __init__.py           (MODIFY: new exports)
```
