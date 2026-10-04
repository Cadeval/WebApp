//! Dependency-free IFC (STEP part 21) SPF parser and single-attribute editor.
//!
//! This crate is compiled twice:
//! - natively, to run the test-suite below with `cargo test`;
//! - to `wasm32-unknown-unknown`, to produce a zero-import WebAssembly module
//!   that a Web Worker drives through the small C ABI exported at the bottom
//!   of this file.
//!
//! The document bytes handed to [`ifc_load`] are kept verbatim. Parsing only
//! records byte ranges (spans) into that buffer, so as long as no attribute
//! is edited, [`ifc_serialize`] reproduces the exact input. Editing splices a
//! replacement token into a scratch copy of the document and re-parses that
//! scratch copy from scratch; the edit is only committed if the whole
//! document is still valid, otherwise the previous state is kept untouched
//! (rollback).

use std::cell::RefCell;

/// Hard limits enforced while parsing, chosen to keep the single-threaded
/// `wasm32-unknown-unknown` module responsive and its memory usage bounded.
const MAX_DOCUMENT_BYTES: usize = 32 * 1024 * 1024;
const MAX_ENTITIES: usize = 100_000;
const MAX_NESTING: usize = 64;

/// Attribute kinds exposed through the ABI.
const KIND_READ_ONLY: i32 = 0;
const KIND_STRING: i32 = 1;
const KIND_NUMBER: i32 = 2;
const KIND_ENUM: i32 = 3;
const KIND_REFERENCE: i32 = 4;
const KIND_SIMPLE: i32 = 5;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
enum ErrorCode {
    InvalidIndex = 2,
    NotFound = 3,
    ReadOnlyAttribute = 4,
    InvalidValue = 5,
    TooLarge = 6,
    TooManyEntities = 7,
    NestingTooDeep = 8,
    MissingMarker = 9,
    UnterminatedString = 10,
    UnterminatedComment = 11,
    UnbalancedParens = 12,
    DuplicateId = 13,
    MissingId = 14,
    MalformedId = 15,
    IdOutOfRange = 16,
    TruncatedStatement = 17,
}

impl ErrorCode {
    fn code(self) -> i32 {
        self as i32
    }
}

/// A single top-level attribute of an indexed entity, stored as a byte span
/// into the owning document plus its editable [`Kind`].
#[derive(Debug, Clone, Copy)]
struct Attr {
    start: usize,
    end: usize,
    kind: i32,
}

/// A simple `#id=TYPE(attrs);` entity indexed from the `DATA` section.
#[derive(Debug, Clone)]
struct Entity {
    id: u32,
    type_start: usize,
    type_end: usize,
    attrs: Vec<Attr>,
}

// ---------------------------------------------------------------------------
// Low level byte scanning helpers. All of them operate on absolute offsets
// into a shared `bytes` slice so spans recorded by callers stay valid.
// ---------------------------------------------------------------------------

/// Advances past a single-quoted STEP string starting at `pos` (which must
/// point at the opening `'`). A doubled `''` inside the string is an escaped
/// literal apostrophe. Returns the offset right after the closing quote.
fn skip_string(bytes: &[u8], pos: usize) -> Result<usize, ErrorCode> {
    let mut pos = pos + 1;
    loop {
        match bytes.get(pos) {
            None => return Err(ErrorCode::UnterminatedString),
            Some(b'\'') => {
                if bytes.get(pos + 1) == Some(&b'\'') {
                    pos += 2;
                } else {
                    return Ok(pos + 1);
                }
            }
            Some(_) => pos += 1,
        }
    }
}

/// Advances past a `/* ... */` comment starting at `pos` (which must point at
/// the leading `/`). Returns the offset right after the closing `*/`.
fn skip_comment(bytes: &[u8], pos: usize) -> Result<usize, ErrorCode> {
    let mut pos = pos + 2;
    let len = bytes.len();
    while pos < len {
        if bytes[pos] == b'*' && bytes.get(pos + 1) == Some(&b'/') {
            return Ok(pos + 2);
        }
        pos += 1;
    }
    Err(ErrorCode::UnterminatedComment)
}

fn is_comment_start(bytes: &[u8], pos: usize) -> bool {
    bytes.get(pos) == Some(&b'/') && bytes.get(pos + 1) == Some(&b'*')
}

/// Skips whitespace and full comments starting at `pos`, stopping at the
/// first significant byte (or at `end`).
fn trim_leading(bytes: &[u8], mut pos: usize, end: usize) -> Result<usize, ErrorCode> {
    while pos < end {
        match bytes[pos] {
            b' ' | b'\t' | b'\r' | b'\n' => pos += 1,
            _ if is_comment_start(bytes, pos) => pos = skip_comment(bytes, pos)?,
            _ => break,
        }
    }
    Ok(pos)
}

/// Trims trailing plain whitespace (not comments) from `[start, end)`.
fn trim_trailing_ws(bytes: &[u8], start: usize, mut end: usize) -> usize {
    while end > start {
        match bytes[end - 1] {
            b' ' | b'\t' | b'\r' | b'\n' => end -= 1,
            _ => break,
        }
    }
    end
}

/// Returns whether `[pos, end)` contains nothing but whitespace and/or full
/// comments.
fn trailing_is_blank(bytes: &[u8], mut pos: usize, end: usize) -> Result<bool, ErrorCode> {
    while pos < end {
        match bytes[pos] {
            b' ' | b'\t' | b'\r' | b'\n' => pos += 1,
            _ if is_comment_start(bytes, pos) => pos = skip_comment(bytes, pos)?,
            _ => return Ok(false),
        }
    }
    Ok(true)
}

/// Splits the whole document into top-level statements (delimited by `;` at
/// paren depth zero, outside strings/comments), validating string/comment
/// termination, paren balance, and the nesting limit along the way. Returns
/// the raw (untrimmed) `[start, end)` span of each statement's content,
/// where `end` is the offset of the terminating `;`.
fn scan_statements(bytes: &[u8]) -> Result<Vec<(usize, usize)>, ErrorCode> {
    let len = bytes.len();
    let mut pos = 0usize;
    let mut depth: usize = 0;
    let mut stmt_start = 0usize;
    let mut statements = Vec::new();

    while pos < len {
        match bytes[pos] {
            b'\'' => pos = skip_string(bytes, pos)?,
            b'/' if is_comment_start(bytes, pos) => pos = skip_comment(bytes, pos)?,
            b'(' => {
                depth += 1;
                if depth > MAX_NESTING {
                    return Err(ErrorCode::NestingTooDeep);
                }
                pos += 1;
            }
            b')' => {
                if depth == 0 {
                    return Err(ErrorCode::UnbalancedParens);
                }
                depth -= 1;
                pos += 1;
            }
            b';' if depth == 0 => {
                statements.push((stmt_start, pos));
                pos += 1;
                stmt_start = pos;
            }
            _ => pos += 1,
        }
    }

    if depth != 0 {
        return Err(ErrorCode::UnbalancedParens);
    }
    if !trailing_is_blank(bytes, stmt_start, len)? {
        return Err(ErrorCode::TruncatedStatement);
    }
    Ok(statements)
}

/// Finds the offset of the `)` matching the `(` at `open_pos`.
fn find_matching_paren(bytes: &[u8], open_pos: usize) -> Result<usize, ErrorCode> {
    let len = bytes.len();
    let mut pos = open_pos + 1;
    let mut depth: usize = 1;
    while pos < len {
        match bytes[pos] {
            b'\'' => pos = skip_string(bytes, pos)?,
            b'/' if is_comment_start(bytes, pos) => pos = skip_comment(bytes, pos)?,
            b'(' => {
                depth += 1;
                pos += 1;
            }
            b')' => {
                depth -= 1;
                if depth == 0 {
                    return Ok(pos);
                }
                pos += 1;
            }
            _ => pos += 1,
        }
    }
    Err(ErrorCode::UnbalancedParens)
}

/// Splits `[start, end)` (the inside of an entity's parens) into trimmed,
/// top-level comma-separated attribute spans.
fn split_top_level_commas(
    bytes: &[u8],
    start: usize,
    end: usize,
) -> Result<Vec<(usize, usize)>, ErrorCode> {
    let mut parts = Vec::new();
    let mut pos = start;
    let mut depth: usize = 0;
    let mut part_start = start;

    while pos < end {
        match bytes[pos] {
            b'\'' => pos = skip_string(bytes, pos)?,
            b'/' if is_comment_start(bytes, pos) => pos = skip_comment(bytes, pos)?,
            b'(' => {
                depth += 1;
                pos += 1;
            }
            b')' => {
                depth = depth.saturating_sub(1);
                pos += 1;
            }
            b',' if depth == 0 => {
                let token_start = trim_leading(bytes, part_start, pos)?;
                let token_end = trim_trailing_ws(bytes, token_start, pos);
                parts.push((token_start, token_end));
                pos += 1;
                part_start = pos;
            }
            _ => pos += 1,
        }
    }
    let token_start = trim_leading(bytes, part_start, end)?;
    let token_end = trim_trailing_ws(bytes, token_start, end);
    parts.push((token_start, token_end));
    Ok(parts)
}

/// Parses the `#<id>=<TYPE>(` prefix of a simple entity statement, returning
/// the entity id plus the byte spans of its type name and the offset of the
/// opening paren.
fn parse_entity_header(
    bytes: &[u8],
    start: usize,
    end: usize,
) -> Result<(u32, usize, usize, usize), ErrorCode> {
    let mut pos = start + 1; // skip '#'
    let id_start = pos;
    while pos < end && bytes[pos].is_ascii_digit() {
        pos += 1;
    }
    let id_end = pos;
    if id_end == id_start {
        return if bytes.get(pos) == Some(&b'=') {
            Err(ErrorCode::MissingId)
        } else {
            Err(ErrorCode::MalformedId)
        };
    }
    pos = trim_leading(bytes, pos, end)?;
    if bytes.get(pos) != Some(&b'=') {
        return Err(ErrorCode::MalformedId);
    }
    pos += 1; // skip '='
    pos = trim_leading(bytes, pos, end)?;

    let type_start = pos;
    while pos < end && (bytes[pos].is_ascii_alphanumeric() || bytes[pos] == b'_') {
        pos += 1;
    }
    let type_end = pos;
    pos = trim_leading(bytes, pos, end)?;
    if type_end == type_start || bytes.get(pos) != Some(&b'(') {
        return Err(ErrorCode::TruncatedStatement);
    }

    let id_str =
        std::str::from_utf8(&bytes[id_start..id_end]).map_err(|_| ErrorCode::MalformedId)?;
    let id: u32 = id_str.parse().map_err(|_| ErrorCode::IdOutOfRange)?;
    Ok((id, type_start, type_end, pos))
}

/// Parses a single `#<id>=<TYPE>(<attrs>);` statement whose content spans
/// `[start, end)` (the `;` is at `end`).
fn parse_entity_statement(bytes: &[u8], start: usize, end: usize) -> Result<Entity, ErrorCode> {
    let (id, type_start, type_end, open_paren) = parse_entity_header(bytes, start, end)?;
    let close_paren = find_matching_paren(bytes, open_paren)?;
    if !trailing_is_blank(bytes, close_paren + 1, end)? {
        return Err(ErrorCode::TruncatedStatement);
    }

    let attrs_start = open_paren + 1;
    let attrs_end = close_paren;
    let blank_start = trim_leading(bytes, attrs_start, attrs_end)?;
    let is_empty = trim_trailing_ws(bytes, blank_start, attrs_end) == blank_start;

    let attrs = if is_empty {
        Vec::new()
    } else {
        split_top_level_commas(bytes, attrs_start, attrs_end)?
            .into_iter()
            .map(|(s, e)| Attr {
                start: s,
                end: e,
                kind: classify(bytes, s, e),
            })
            .collect()
    };

    Ok(Entity {
        id,
        type_start,
        type_end,
        attrs,
    })
}

/// Classifies the trimmed token `bytes[start..end]` into one of the editable
/// kinds, or [`KIND_READ_ONLY`] for lists, wrapped/complex values, or any
/// value that does not fully match a supported grammar.
fn classify(bytes: &[u8], start: usize, end: usize) -> i32 {
    if start >= end {
        return KIND_READ_ONLY;
    }
    let slice = &bytes[start..end];

    if slice == b"$" || slice == b"*" {
        return KIND_SIMPLE;
    }
    if slice[0] == b'\'' {
        return match skip_string(bytes, start) {
            Ok(pos) if pos == end => KIND_STRING,
            _ => KIND_READ_ONLY,
        };
    }
    if slice[0] == b'.' && slice.len() >= 3 && slice[slice.len() - 1] == b'.' {
        let inner = &slice[1..slice.len() - 1];
        let valid = inner
            .iter()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || *b == b'_');
        return if valid { KIND_ENUM } else { KIND_READ_ONLY };
    }
    if slice[0] == b'#' {
        let digits = &slice[1..];
        let valid = !digits.is_empty() && digits.iter().all(|b| b.is_ascii_digit());
        return if valid && std::str::from_utf8(digits).unwrap().parse::<u32>().is_ok() {
            KIND_REFERENCE
        } else {
            KIND_READ_ONLY
        };
    }
    if is_step_number(slice) {
        return KIND_NUMBER;
    }
    KIND_READ_ONLY
}

/// Returns whether `slice` is a finite STEP integer or real number, matching
/// the grammar `['-'|'+'] DIGIT+ ['.' DIGIT*] [('e'|'E') ['-'|'+'] DIGIT+]`.
fn is_step_number(slice: &[u8]) -> bool {
    let len = slice.len();
    let mut i = 0;

    if i < len && (slice[i] == b'-' || slice[i] == b'+') {
        i += 1;
    }
    let int_start = i;
    while i < len && slice[i].is_ascii_digit() {
        i += 1;
    }
    let has_int_digits = i > int_start;
    if !has_int_digits {
        return false;
    }

    if i < len && slice[i] == b'.' {
        i += 1;
        while i < len && slice[i].is_ascii_digit() {
            i += 1;
        }
    }

    if i < len && (slice[i] == b'e' || slice[i] == b'E') {
        i += 1;
        if i < len && (slice[i] == b'-' || slice[i] == b'+') {
            i += 1;
        }
        let exp_start = i;
        while i < len && slice[i].is_ascii_digit() {
            i += 1;
        }
        if i == exp_start {
            return false;
        }
    }

    if i != len {
        return false;
    }
    std::str::from_utf8(slice)
        .ok()
        .and_then(|text| text.parse::<f64>().ok())
        .is_some_and(|value| value.is_finite())
}

fn is_marker(bytes: &[u8], span: (usize, usize), expected: &[u8]) -> Result<bool, ErrorCode> {
    let start = trim_leading(bytes, span.0, span.1)?;
    let end = trim_trailing_ws(bytes, start, span.1);
    Ok(&bytes[start..end] == expected)
}

fn check_marker(bytes: &[u8], span: (usize, usize), expected: &[u8]) -> Result<(), ErrorCode> {
    if is_marker(bytes, span, expected)? {
        Ok(())
    } else {
        Err(ErrorCode::MissingMarker)
    }
}

/// Parses a full IFC SPF document, validating structure and indexing every
/// simple `#id=TYPE(attrs);` entity found in the `DATA` section.
fn parse_document(bytes: &[u8]) -> Result<Vec<Entity>, ErrorCode> {
    if bytes.len() > MAX_DOCUMENT_BYTES {
        return Err(ErrorCode::TooLarge);
    }

    let statements = scan_statements(bytes)?;
    if statements.len() < 2 {
        return Err(ErrorCode::MissingMarker);
    }

    check_marker(bytes, statements[0], b"ISO-10303-21")?;
    check_marker(bytes, statements[1], b"HEADER")?;

    let mut i = 2;
    loop {
        if i >= statements.len() {
            return Err(ErrorCode::MissingMarker);
        }
        if is_marker(bytes, statements[i], b"ENDSEC")? {
            break;
        }
        i += 1;
    }
    i += 1;

    if i >= statements.len() {
        return Err(ErrorCode::MissingMarker);
    }
    check_marker(bytes, statements[i], b"DATA")?;
    i += 1;

    let mut entities: Vec<Entity> = Vec::new();
    loop {
        if i >= statements.len() {
            return Err(ErrorCode::MissingMarker);
        }
        let (s, e) = statements[i];
        if is_marker(bytes, (s, e), b"ENDSEC")? {
            break;
        }
        let content_start = trim_leading(bytes, s, e)?;
        if bytes.get(content_start) == Some(&b'#') {
            let entity = parse_entity_statement(bytes, content_start, e)?;
            if entities.len() >= MAX_ENTITIES {
                return Err(ErrorCode::TooManyEntities);
            }
            entities.push(entity);
        }
        i += 1;
    }

    check_duplicate_ids(&entities)?;
    Ok(entities)
}

fn check_duplicate_ids(entities: &[Entity]) -> Result<(), ErrorCode> {
    let mut ids: Vec<u32> = entities.iter().map(|entity| entity.id).collect();
    ids.sort_unstable();
    if ids.windows(2).any(|pair| pair[0] == pair[1]) {
        return Err(ErrorCode::DuplicateId);
    }
    Ok(())
}

/// Builds a scratch document with `entity.attrs[attr_index]` replaced by
/// `new_value` (already trimmed), without mutating `doc`.
fn splice_attribute(doc: &[u8], attr: &Attr, new_value: &[u8]) -> Vec<u8> {
    let mut scratch = Vec::with_capacity(doc.len() - (attr.end - attr.start) + new_value.len());
    scratch.extend_from_slice(&doc[..attr.start]);
    scratch.extend_from_slice(new_value);
    scratch.extend_from_slice(&doc[attr.end..]);
    scratch
}

// ---------------------------------------------------------------------------
// Module-owned state and the zero-import WebAssembly ABI.
// ---------------------------------------------------------------------------

struct State {
    document: Vec<u8>,
    entities: Vec<Entity>,
    input: Vec<u8>,
    output: Vec<u8>,
    last_error: i32,
}

impl State {
    const fn new() -> Self {
        State {
            document: Vec::new(),
            entities: Vec::new(),
            input: Vec::new(),
            output: Vec::new(),
            last_error: 0,
        }
    }
}

thread_local! {
    static STATE: RefCell<State> = const { RefCell::new(State::new()) };
}

fn fail(state: &mut State, code: ErrorCode) -> i32 {
    state.last_error = code.code();
    -code.code()
}

/// Reserves a module-owned input buffer of `len` bytes and returns a pointer
/// the host can write `len` bytes into before calling [`ifc_load`] or
/// [`ifc_update_attribute`].
///
/// The pointer is returned as `usize` rather than a fixed-width `u32` so that
/// this same function is safe to call from native tests (where pointers are
/// 64-bit); on the `wasm32-unknown-unknown` target `usize` is exactly the
/// 32-bit address the exported ABI requires.
/// Requests above the document limit return `usize::MAX` (`-1` in the WASM
/// ABI) and preserve the previous input buffer and loaded document.
#[no_mangle]
pub extern "C" fn ifc_input_reserve(len: u32) -> usize {
    STATE.with(|state| {
        let mut state = state.borrow_mut();
        if len as usize > MAX_DOCUMENT_BYTES {
            fail(&mut state, ErrorCode::TooLarge);
            return usize::MAX;
        }
        state.input = vec![0u8; len as usize];
        state.input.as_mut_ptr() as usize
    })
}

/// Parses the first `len` bytes of the reserved input buffer as an IFC SPF
/// document, replacing any previously loaded document on success.
#[no_mangle]
pub extern "C" fn ifc_load(len: u32) -> i32 {
    STATE.with(|state| {
        let mut state = state.borrow_mut();
        let len = len as usize;
        if len > state.input.len() {
            return fail(&mut state, ErrorCode::InvalidValue);
        }
        let bytes = state.input[..len].to_vec();
        match parse_document(&bytes) {
            Ok(entities) => {
                state.document = bytes;
                state.entities = entities;
                state.last_error = 0;
                0
            }
            Err(code) => fail(&mut state, code),
        }
    })
}

/// Number of indexed entities in the currently loaded document.
#[no_mangle]
pub extern "C" fn ifc_entity_count() -> u32 {
    STATE.with(|state| state.borrow().entities.len() as u32)
}

/// The STEP id of the entity at `index`, or `0` (with `last_error` set) if
/// `index` is out of range.
#[no_mangle]
pub extern "C" fn ifc_entity_id(index: u32) -> u32 {
    STATE.with(|state| {
        let mut state = state.borrow_mut();
        match state.entities.get(index as usize) {
            Some(entity) => entity.id,
            None => {
                fail(&mut state, ErrorCode::InvalidIndex);
                0
            }
        }
    })
}

/// Copies the entity's type name into the shared output buffer and returns
/// its length, or a negative error code.
#[no_mangle]
pub extern "C" fn ifc_entity_type(index: u32) -> i32 {
    STATE.with(|state| {
        let mut state = state.borrow_mut();
        let span = match state.entities.get(index as usize) {
            Some(entity) => (entity.type_start, entity.type_end),
            None => return fail(&mut state, ErrorCode::InvalidIndex),
        };
        state.output = state.document[span.0..span.1].to_vec();
        state.last_error = 0;
        state.output.len() as i32
    })
}

/// Number of top-level attributes of the entity at `index`, or a negative
/// error code.
#[no_mangle]
pub extern "C" fn ifc_entity_attribute_count(index: u32) -> i32 {
    STATE.with(|state| {
        let mut state = state.borrow_mut();
        match state
            .entities
            .get(index as usize)
            .map(|entity| entity.attrs.len())
        {
            Some(count) => {
                state.last_error = 0;
                count as i32
            }
            None => fail(&mut state, ErrorCode::InvalidIndex),
        }
    })
}

/// The [`Kind`] of an attribute, or a negative error code.
#[no_mangle]
pub extern "C" fn ifc_entity_attribute_kind(entity_index: u32, attr_index: u32) -> i32 {
    STATE.with(|state| {
        let mut state = state.borrow_mut();
        let kind = state
            .entities
            .get(entity_index as usize)
            .and_then(|entity| entity.attrs.get(attr_index as usize))
            .map(|attr| attr.kind);
        match kind {
            Some(kind) => {
                state.last_error = 0;
                kind
            }
            None => fail(&mut state, ErrorCode::InvalidIndex),
        }
    })
}

/// Copies an attribute's exact source bytes into the shared output buffer
/// and returns its length, or a negative error code.
#[no_mangle]
pub extern "C" fn ifc_entity_attribute(entity_index: u32, attr_index: u32) -> i32 {
    STATE.with(|state| {
        let mut state = state.borrow_mut();
        let span = match state
            .entities
            .get(entity_index as usize)
            .and_then(|entity| entity.attrs.get(attr_index as usize))
        {
            Some(attr) => (attr.start, attr.end),
            None => return fail(&mut state, ErrorCode::InvalidIndex),
        };
        state.output = state.document[span.0..span.1].to_vec();
        state.last_error = 0;
        state.output.len() as i32
    })
}

/// The index of the entity with the given STEP id, or a negative error code
/// if none is loaded with that id.
#[no_mangle]
pub extern "C" fn ifc_find_entity(id: u32) -> i32 {
    STATE.with(|state| {
        let mut state = state.borrow_mut();
        match state.entities.iter().position(|entity| entity.id == id) {
            Some(index) => {
                state.last_error = 0;
                index as i32
            }
            None => fail(&mut state, ErrorCode::NotFound),
        }
    })
}

/// Replaces the attribute at `(entity_index, attr_index)` with the first
/// `value_len` bytes of the reserved input buffer. The whole document is
/// spliced and fully re-parsed before the edit is committed; on any failure
/// the previous document is kept unchanged (rollback).
#[no_mangle]
pub extern "C" fn ifc_update_attribute(entity_index: u32, attr_index: u32, value_len: u32) -> i32 {
    STATE.with(|state| {
        let mut state = state.borrow_mut();

        let attr = match state
            .entities
            .get(entity_index as usize)
            .and_then(|entity| entity.attrs.get(attr_index as usize))
        {
            Some(attr) => *attr,
            None => return fail(&mut state, ErrorCode::InvalidIndex),
        };
        if attr.kind == KIND_READ_ONLY {
            return fail(&mut state, ErrorCode::ReadOnlyAttribute);
        }

        let value_len = value_len as usize;
        if value_len > state.input.len() {
            return fail(&mut state, ErrorCode::InvalidValue);
        }
        let raw_value = &state.input[..value_len];
        let trim_start = trim_leading(raw_value, 0, raw_value.len()).unwrap_or(0);
        let trim_end = trim_trailing_ws(raw_value, trim_start, raw_value.len());
        let new_value = &raw_value[trim_start..trim_end];
        if new_value.is_empty() || classify(new_value, 0, new_value.len()) == KIND_READ_ONLY {
            return fail(&mut state, ErrorCode::InvalidValue);
        }

        let scratch = splice_attribute(&state.document, &attr, new_value);
        match parse_document(&scratch) {
            Ok(entities) => {
                state.document = scratch;
                state.entities = entities;
                state.last_error = 0;
                0
            }
            Err(code) => fail(&mut state, code),
        }
    })
}

/// Copies the current document into the shared output buffer and returns
/// its length. Without edits this is byte-identical to the loaded input.
#[no_mangle]
pub extern "C" fn ifc_serialize() -> i32 {
    STATE.with(|state| {
        let mut state = state.borrow_mut();
        state.output = state.document.clone();
        state.last_error = 0;
        state.output.len() as i32
    })
}

/// Pointer to the shared output buffer, valid until the next call that
/// produces output overwrites it. See [`ifc_input_reserve`] for why this is
/// `usize` rather than a fixed-width `u32`.
#[no_mangle]
pub extern "C" fn ifc_output_ptr() -> usize {
    STATE.with(|state| state.borrow().output.as_ptr() as usize)
}

/// The error code set by the most recent failing call.
#[no_mangle]
pub extern "C" fn ifc_last_error() -> i32 {
    STATE.with(|state| state.borrow().last_error)
}

#[cfg(test)]
#[path = "../../../tests/rust/example_plugin/lib.rs"]
mod tests;
