// Deep standard-library usage. Where features.rs aims at grammar corners, this file aims
// at the long, backreference-heavy names a real program produces: nested container
// monomorphisations, blanket impls, vtables, drop glue, formatting machinery and threads.
#![allow(dead_code, unused_variables, unused_must_use, clippy::all)]

use std::borrow::Cow;
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, HashMap};
use std::fmt::Debug;
use std::marker::PhantomData;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

// ------------------------------------------------------ deeply nested generics

pub type Deep = HashMap<String, BTreeMap<u32, Vec<Option<Box<[Result<i8, String>]>>>>>;

#[inline(never)]
pub fn deep_build(n: usize) -> Deep {
    let mut top: Deep = HashMap::new();
    let mut mid: BTreeMap<u32, Vec<Option<Box<[Result<i8, String>]>>>> = BTreeMap::new();
    mid.insert(1, vec![Some(vec![Ok(1i8), Err(String::from("e"))].into_boxed_slice())]);
    top.insert(String::from("k"), mid);
    top
}

#[inline(never)]
pub fn deep_walk(d: &Deep) -> usize {
    d.values().flat_map(|m| m.values()).flatten().flatten().map(|b| b.len()).sum()
}

pub struct Tagged<T, Tag> {
    pub value: T,
    marker: PhantomData<Tag>,
}

pub struct Meters;
pub struct Seconds;

impl<T: Clone, Tag> Tagged<T, Tag> {
    #[inline(never)]
    pub fn new(value: T) -> Self {
        Tagged { value, marker: PhantomData }
    }
    #[inline(never)]
    pub fn retag<Other>(&self) -> Tagged<T, Other> {
        Tagged { value: self.value.clone(), marker: PhantomData }
    }
}

// ------------------------------------------------------- supertraits, blankets

pub trait Named {
    fn name(&self) -> Cow<'_, str>;
}

pub trait Described: Named + Debug {
    #[inline(never)]
    fn describe(&self) -> String {
        format!("{}={:?}", self.name(), self)
    }
}

impl<T: Named + Debug> Described for T {}

#[derive(Debug)]
pub struct Widget(pub u32);

impl Named for Widget {
    #[inline(never)]
    fn name(&self) -> Cow<'_, str> {
        if self.0 == 0 { Cow::Borrowed("zero") } else { Cow::Owned(format!("w{}", self.0)) }
    }
}

#[derive(Debug)]
pub struct Gadget<T: Debug>(pub T);

impl<T: Debug> Named for Gadget<T> {
    #[inline(never)]
    fn name(&self) -> Cow<'_, str> {
        Cow::Owned(format!("{:?}", self.0))
    }
}

// ------------------------------------------------------------------ where-clauses

#[inline(never)]
pub fn constrained<I, T, E>(iter: I) -> Result<Vec<T>, E>
where
    I: IntoIterator<Item = Result<T, E>>,
    T: Ord + Clone,
    E: Debug,
{
    let mut out: Vec<T> = iter.into_iter().collect::<Result<Vec<T>, E>>()?;
    out.sort();
    Ok(out)
}

#[inline(never)]
pub fn callback_table() -> Vec<Box<dyn Fn(&str) -> String + Send + 'static>> {
    let upper: Box<dyn Fn(&str) -> String + Send> = Box::new(|s| s.to_uppercase());
    let repeat: Box<dyn Fn(&str) -> String + Send> = Box::new(|s| s.repeat(2));
    vec![upper, repeat]
}

#[inline(never)]
pub fn dispatch(table: &[Box<dyn Fn(&str) -> String + Send + 'static>], s: &str) -> usize {
    table.iter().map(|f| f(s).len()).sum()
}

// ------------------------------------------------------------- interior mutability

thread_local! {
    static COUNTER: RefCell<HashMap<&'static str, u64>> = RefCell::new(HashMap::new());
}

pub static GLOBAL: AtomicUsize = AtomicUsize::new(0);

#[inline(never)]
pub fn bump(key: &'static str) -> u64 {
    GLOBAL.fetch_add(1, Ordering::Relaxed);
    COUNTER.with(|c| {
        let mut map = c.borrow_mut();
        let slot = map.entry(key).or_insert(0);
        *slot += 1;
        *slot
    })
}

#[inline(never)]
pub fn cells() -> (Cell<u8>, RefCell<Vec<Cell<u8>>>) {
    (Cell::new(1), RefCell::new(vec![Cell::new(2)]))
}

// ------------------------------------------------------------------------ threads

#[inline(never)]
pub fn spawn_workers(n: usize) -> usize {
    let shared = Arc::new(AtomicUsize::new(0));
    let handles: Vec<_> = (0..n)
        .map(|i| {
            let mine = Arc::clone(&shared);
            std::thread::spawn(move || {
                mine.fetch_add(i, Ordering::SeqCst);
                i * 2
            })
        })
        .collect();
    handles.into_iter().map(|h| h.join().unwrap_or(0)).sum()
}

// -------------------------------------------------------------- slices and strings

#[inline(never)]
pub fn chunking(data: &[u8]) -> Vec<Vec<u8>> {
    data.chunks(3).map(|c| c.to_vec()).collect()
}

#[inline(never)]
pub fn windowing(data: &[i32]) -> Vec<i32> {
    data.windows(2).map(|w| w[0] + w[1]).collect()
}

#[inline(never)]
pub fn text_work(input: &str) -> Vec<Cow<'_, str>> {
    input
        .split_whitespace()
        .filter(|w| !w.is_empty())
        .map(|w| if w.contains('_') { Cow::Owned(w.replace('_', "-")) } else { Cow::Borrowed(w) })
        .collect()
}

#[inline(never)]
pub fn binary_search_by_key(data: &[(u32, &str)], key: u32) -> Result<usize, usize> {
    data.binary_search_by_key(&key, |(k, _)| *k)
}

// --------------------------------------------------------------------- error types

#[derive(Debug)]
pub enum AppError {
    Io(std::io::Error),
    Parse(std::num::ParseIntError),
    Custom { detail: String, code: u16 },
}

impl std::fmt::Display for AppError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AppError::Io(e) => write!(f, "io: {e}"),
            AppError::Parse(e) => write!(f, "parse: {e}"),
            AppError::Custom { detail, code } => write!(f, "{code}: {detail}"),
        }
    }
}

impl std::error::Error for AppError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            AppError::Io(e) => Some(e),
            AppError::Parse(e) => Some(e),
            AppError::Custom { .. } => None,
        }
    }
}

impl From<std::num::ParseIntError> for AppError {
    #[inline(never)]
    fn from(e: std::num::ParseIntError) -> Self {
        AppError::Parse(e)
    }
}

#[inline(never)]
pub fn fallible(input: &str) -> Result<u64, AppError> {
    let n: u64 = input.trim().parse()?;
    if n == 0 {
        return Err(AppError::Custom { detail: String::from("zero"), code: 400 });
    }
    Ok(n)
}

#[inline(never)]
pub fn boxed_error(input: &str) -> Result<u64, Box<dyn std::error::Error + Send + Sync>> {
    Ok(fallible(input)?)
}

// ------------------------------------------------------------------- recursion

#[inline(never)]
pub fn recursive<T: Clone + Debug>(items: &[T], depth: usize) -> usize {
    if depth == 0 || items.is_empty() {
        return items.len();
    }
    recursive(&items[1..], depth - 1) + format!("{:?}", items[0]).len()
}

// ----------------------------------------------------------------- extern "C"

pub extern "C" fn c_abi(a: u32, b: u32) -> u32 {
    a.wrapping_add(b)
}

pub extern "system" fn system_abi(a: u16) -> u16 {
    a
}

// ------------------------------------------------------------------ generic enum

#[derive(Debug, Clone)]
pub enum Tree<T> {
    Leaf(T),
    Node(Box<Tree<T>>, Box<Tree<T>>),
}

impl<T: Clone + Debug + Ord> Tree<T> {
    #[inline(never)]
    pub fn depth(&self) -> usize {
        match self {
            Tree::Leaf(_) => 1,
            Tree::Node(l, r) => 1 + l.depth().max(r.depth()),
        }
    }
    #[inline(never)]
    pub fn flatten(&self, out: &mut Vec<T>) {
        match self {
            Tree::Leaf(v) => out.push(v.clone()),
            Tree::Node(l, r) => {
                l.flatten(out);
                r.flatten(out);
            }
        }
    }
}

pub fn exercise() -> usize {
    let mut total = 0usize;
    let d = deep_build(1);
    total += deep_walk(&d);

    total += Tagged::<f64, Meters>::new(1.0).retag::<Seconds>().value as usize;
    total += Tagged::<Vec<u8>, Seconds>::new(vec![1]).value.len();

    total += Widget(0).describe().len();
    total += Widget(7).describe().len();
    total += Gadget(vec![1u8]).describe().len();
    total += Gadget("s").describe().len();

    total += constrained::<_, u32, String>(vec![Ok(3u32), Ok(1)]).unwrap().len();
    total += constrained::<_, String, u8>(vec![Ok(String::from("a"))]).unwrap().len();

    let table = callback_table();
    total += dispatch(&table, "abc");

    total += bump("a") as usize;
    total += cells().0.get() as usize;
    total += spawn_workers(2);

    total += chunking(&[1, 2, 3, 4]).len();
    total += windowing(&[1, 2, 3]).len();
    total += text_work("a b_c").len();
    total += binary_search_by_key(&[(1, "a")], 1).unwrap_or(0);

    total += fallible(" 12 ").unwrap_or(0) as usize;
    total += boxed_error("3").unwrap_or(0) as usize;
    total += recursive(&[1u8, 2, 3], 2);
    total += recursive(&["a", "b"], 1);

    total += c_abi(1, 2) as usize;
    total += system_abi(3) as usize;

    let t = Tree::Node(Box::new(Tree::Leaf(1u16)), Box::new(Tree::Leaf(2)));
    let mut flat = Vec::new();
    t.flatten(&mut flat);
    total += t.depth() + flat.len();
    total
}

fn main() {
    println!("{}", exercise());
}
