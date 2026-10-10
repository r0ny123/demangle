#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <structmember.h>

static PyObject *ParseError_class = NULL;
static PyObject *TruncatedError_class = NULL;

static int init_errors(void) {
    if (ParseError_class != NULL) return 0;
    PyObject *mod = PyImport_ImportModule("demangle.core.errors");
    if (!mod) return -1;
    ParseError_class = PyObject_GetAttrString(mod, "ParseError");
    TruncatedError_class = PyObject_GetAttrString(mod, "TruncatedError");
    Py_DECREF(mod);
    return (ParseError_class && TruncatedError_class) ? 0 : -1;
}

static PyObject* raise_parse_error(PyObject *text, Py_ssize_t pos, const char *msg) {
    if (init_errors() < 0) return NULL;
    PyObject *py_pos = PyLong_FromSsize_t(pos);
    PyObject *py_msg = PyUnicode_FromString(msg);
    if (!py_pos || !py_msg) {
        Py_XDECREF(py_pos);
        Py_XDECREF(py_msg);
        return NULL;
    }
    PyObject *err = PyObject_CallFunction(ParseError_class, "OOO", text, py_pos, py_msg);
    Py_DECREF(py_pos);
    Py_DECREF(py_msg);
    if (err) {
        PyErr_SetObject(ParseError_class, err);
        Py_DECREF(err);
    }
    return NULL;
}

static PyObject* raise_truncated_error(PyObject *text, Py_ssize_t pos) {
    if (init_errors() < 0) return NULL;
    PyObject *py_pos = PyLong_FromSsize_t(pos);
    if (!py_pos) return NULL;
    PyObject *err = PyObject_CallFunction(TruncatedError_class, "OO", text, py_pos);
    Py_DECREF(py_pos);
    if (err) {
        PyErr_SetObject(TruncatedError_class, err);
        Py_DECREF(err);
    }
    return NULL;
}

typedef struct {
    PyObject_HEAD
    PyObject *text;
    Py_ssize_t pos;
    Py_ssize_t length;
    int padded_length;
    int kind;
    const void *data;
} FastReaderObject;

static void FastReader_dealloc(FastReaderObject *self) {
    Py_XDECREF(self->text);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static int FastReader_init(FastReaderObject *self, PyObject *args, PyObject *kwds) {
    static char *kwlist[] = {"text", NULL};
    PyObject *text_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "U", kwlist, &text_obj)) {
        return -1;
    }
    Py_XDECREF(self->text);
    Py_INCREF(text_obj);
    self->text = text_obj;
    self->pos = 0;
    self->length = PyUnicode_GET_LENGTH(text_obj);
    self->padded_length = 0;
    self->kind = PyUnicode_KIND(text_obj);
    self->data = PyUnicode_DATA(text_obj);
    return 0;
}

static PyObject* FastReader_peek(FastReaderObject *self, PyObject *Py_UNUSED(ignored)) {
    if (self->pos < self->length) {
        Py_UCS4 ch = PyUnicode_READ(self->kind, self->data, self->pos);
        return PyUnicode_FromOrdinal(ch);
    }
    return PyUnicode_New(0, 0);
}

static PyObject* FastReader_ahead(FastReaderObject *self, PyObject *args) {
    Py_ssize_t offset;
    if (!PyArg_ParseTuple(args, "n", &offset)) return NULL;
    Py_ssize_t idx = self->pos + offset;
    if (idx >= 0 && idx < self->length) {
        Py_UCS4 ch = PyUnicode_READ(self->kind, self->data, idx);
        return PyUnicode_FromOrdinal(ch);
    }
    return PyUnicode_New(0, 0);
}

static PyObject* FastReader_peek2(FastReaderObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_ssize_t pos = self->pos;
    if (pos >= self->length) {
        return PyUnicode_New(0, 0);
    }
    Py_ssize_t end = pos + 2;
    if (end > self->length) end = self->length;
    return PyUnicode_Substring(self->text, pos, end);
}

static PyObject* FastReader_ahead2(FastReaderObject *self, PyObject *args) {
    Py_ssize_t offset;
    if (!PyArg_ParseTuple(args, "n", &offset)) return NULL;
    Py_ssize_t start = self->pos + offset;
    if (start < 0 || start >= self->length) {
        return PyUnicode_New(0, 0);
    }
    Py_ssize_t end = start + 2;
    if (end > self->length) end = self->length;
    return PyUnicode_Substring(self->text, start, end);
}

static PyObject* FastReader_startswith(FastReaderObject *self, PyObject *args) {
    PyObject *lit;
    if (!PyArg_ParseTuple(args, "U", &lit)) return NULL;
    Py_ssize_t lit_len = PyUnicode_GET_LENGTH(lit);
    if (self->pos + lit_len <= self->length) {
        if (PyUnicode_Tailmatch(self->text, lit, self->pos, self->pos + lit_len, -1) == 1) {
            Py_RETURN_TRUE;
        }
    }
    Py_RETURN_FALSE;
}

static PyObject* FastReader_take(FastReaderObject *self, PyObject *Py_UNUSED(ignored)) {
    if (self->pos >= self->length) {
        return raise_truncated_error(self->text, self->pos);
    }
    Py_UCS4 ch = PyUnicode_READ(self->kind, self->data, self->pos);
    self->pos++;
    return PyUnicode_FromOrdinal(ch);
}

static PyObject* FastReader_take_exactly(FastReaderObject *self, PyObject *args) {
    Py_ssize_t count;
    if (!PyArg_ParseTuple(args, "n", &count)) return NULL;
    Py_ssize_t end = self->pos + count;
    if (count < 0 || end > self->length) {
        return raise_truncated_error(self->text, self->pos);
    }
    PyObject *chunk = PyUnicode_Substring(self->text, self->pos, end);
    self->pos = end;
    return chunk;
}

static PyObject* FastReader_eat(FastReaderObject *self, PyObject *args) {
    PyObject *lit;
    if (!PyArg_ParseTuple(args, "U", &lit)) return NULL;
    Py_ssize_t lit_len = PyUnicode_GET_LENGTH(lit);
    if (lit_len == 1) {
        if (self->pos < self->length) {
            Py_UCS4 ch1 = PyUnicode_READ(self->kind, self->data, self->pos);
            Py_UCS4 ch2 = PyUnicode_READ(PyUnicode_KIND(lit), PyUnicode_DATA(lit), 0);
            if (ch1 == ch2) {
                self->pos++;
                Py_RETURN_TRUE;
            }
        }
        Py_RETURN_FALSE;
    }
    if (self->pos + lit_len <= self->length) {
        if (PyUnicode_Tailmatch(self->text, lit, self->pos, self->pos + lit_len, -1) == 1) {
            self->pos += lit_len;
            Py_RETURN_TRUE;
        }
    }
    Py_RETURN_FALSE;
}

static PyObject* FastReader_expect(FastReaderObject *self, PyObject *args) {
    PyObject *lit;
    if (!PyArg_ParseTuple(args, "U", &lit)) return NULL;
    Py_ssize_t lit_len = PyUnicode_GET_LENGTH(lit);
    Py_ssize_t pos = self->pos;
    if (lit_len == 1) {
        if (pos < self->length) {
            Py_UCS4 ch1 = PyUnicode_READ(self->kind, self->data, pos);
            Py_UCS4 ch2 = PyUnicode_READ(PyUnicode_KIND(lit), PyUnicode_DATA(lit), 0);
            if (ch1 == ch2) {
                self->pos = pos + 1;
                Py_RETURN_NONE;
            }
        }
    } else {
        if (pos + lit_len <= self->length) {
            if (PyUnicode_Tailmatch(self->text, lit, pos, pos + lit_len, -1) == 1) {
                self->pos = pos + lit_len;
                Py_RETURN_NONE;
            }
        }
    }
    PyObject *rep = PyObject_Repr(lit);
    char buf[128];
    if (rep && PyUnicode_Check(rep)) {
        snprintf(buf, sizeof(buf), "expected %s", PyUnicode_AsUTF8(rep));
        Py_DECREF(rep);
    } else {
        snprintf(buf, sizeof(buf), "expected literal");
        Py_XDECREF(rep);
    }
    return raise_parse_error(self->text, pos, buf);
}

static PyObject* FastReader_digits(FastReaderObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_ssize_t start = self->pos;
    Py_ssize_t pos = start;
    Py_ssize_t len = self->length;
    int kind = self->kind;
    const void *data = self->data;
    while (pos < len) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, pos);
        if (ch >= '0' && ch <= '9') {
            pos++;
            if (pos - start > 20) {
                return raise_parse_error(self->text, start, "number too long");
            }
        } else {
            break;
        }
    }
    if (pos == start) {
        return raise_parse_error(self->text, start, "expected a number");
    }
    self->pos = pos;
    return PyUnicode_Substring(self->text, start, pos);
}

static PyObject* FastReader_length_prefixed(FastReaderObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_ssize_t start = self->pos;
    Py_ssize_t pos = start;
    Py_ssize_t len = self->length;
    int kind = self->kind;
    const void *data = self->data;
    while (pos < len) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, pos);
        if (ch >= '0' && ch <= '9') {
            pos++;
            if (pos - start > 20) {
                return raise_parse_error(self->text, start, "number too long");
            }
        } else {
            break;
        }
    }
    if (pos == start) {
        return raise_parse_error(self->text, start, "expected a number");
    }
    if (PyUnicode_READ(kind, data, start) == '0') {
        self->padded_length = 1;
    }
    long long count = 0;
    for (Py_ssize_t i = start; i < pos; i++) {
        count = count * 10 + (PyUnicode_READ(kind, data, i) - '0');
    }
    Py_ssize_t end = pos + count;
    if (end > len) {
        return raise_truncated_error(self->text, pos);
    }
    self->pos = end;
    PyObject *chunk = PyUnicode_Substring(self->text, pos, end);
    if (!chunk) return NULL;
    PyObject *py_count = PyLong_FromLongLong(count);
    if (!py_count) {
        Py_DECREF(chunk);
        return NULL;
    }
    PyObject *res = PyTuple_Pack(2, py_count, chunk);
    Py_DECREF(py_count);
    Py_DECREF(chunk);
    return res;
}

static PyObject* FastReader_number(FastReaderObject *self, PyObject *args, PyObject *kwds) {
    static char *kwlist[] = {"allow_negative", NULL};
    int allow_neg = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|p", kwlist, &allow_neg)) {
        return NULL;
    }
    int negative = 0;
    if (allow_neg && self->pos < self->length && PyUnicode_READ(self->kind, self->data, self->pos) == 'n') {
        negative = 1;
        self->pos++;
    }
    PyObject *digits_obj = FastReader_digits(self, NULL);
    if (!digits_obj) return NULL;
    if (!negative) return digits_obj;

    PyObject *dash = PyUnicode_FromString("-");
    PyObject *res = PyUnicode_Concat(dash, digits_obj);
    Py_DECREF(dash);
    Py_DECREF(digits_obj);
    return res;
}

static PyObject* FastReader_integer(FastReaderObject *self, PyObject *args, PyObject *kwds) {
    PyObject *num_str = FastReader_number(self, args, kwds);
    if (!num_str) return NULL;
    PyObject *res = PyLong_FromUnicodeObject(num_str, 10);
    Py_DECREF(num_str);
    return res;
}

static PyObject* FastReader_seq_id(FastReaderObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_ssize_t start = self->pos;
    Py_ssize_t pos = start;
    Py_ssize_t len = self->length;
    int kind = self->kind;
    const void *data = self->data;
    while (pos < len) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, pos);
        if ((ch >= '0' && ch <= '9') || (ch >= 'A' && ch <= 'Z')) {
            pos++;
            if (pos - start > 12) {
                return raise_parse_error(self->text, start, "substitution index too long");
            }
        } else {
            break;
        }
    }
    if (pos >= len || PyUnicode_READ(kind, data, pos) != '_') {
        self->pos = pos;
        return raise_parse_error(self->text, pos, "expected '_'");
    }
    self->pos = pos + 1;
    if (pos == start) {
        return PyLong_FromLong(0);
    }
    long long value = 0;
    for (Py_ssize_t i = start; i < pos; i++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, i);
        int v = (ch >= '0' && ch <= '9') ? (ch - '0') : (ch - 'A' + 10);
        value = value * 36 + v;
    }
    return PyLong_FromLongLong(value + 1);
}

static PyObject* FastReader_fail(FastReaderObject *self, PyObject *args) {
    const char *msg;
    if (!PyArg_ParseTuple(args, "s", &msg)) return NULL;
    return raise_parse_error(self->text, self->pos, msg);
}

static PyObject* FastReader_get_eof(FastReaderObject *self, void *closure) {
    if (self->pos >= self->length) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

static PyObject* FastReader_get_remaining(FastReaderObject *self, void *closure) {
    if (self->pos >= self->length) {
        return PyUnicode_New(0, 0);
    }
    return PyUnicode_Substring(self->text, self->pos, self->length);
}

static PyObject* FastReader_get_text(FastReaderObject *self, void *closure) {
    Py_INCREF(self->text);
    return self->text;
}

static int FastReader_set_text(FastReaderObject *self, PyObject *val, void *closure) {
    if (!val || !PyUnicode_Check(val)) {
        PyErr_SetString(PyExc_TypeError, "text must be a string");
        return -1;
    }
    Py_XDECREF(self->text);
    Py_INCREF(val);
    self->text = val;
    self->kind = PyUnicode_KIND(val);
    self->data = PyUnicode_DATA(val);
    self->pos = 0;
    self->length = PyUnicode_GET_LENGTH(val);
    return 0;
}

static PyObject* FastReader_get_pos(FastReaderObject *self, void *closure) {
    return PyLong_FromSsize_t(self->pos);
}

static int FastReader_set_pos(FastReaderObject *self, PyObject *val, void *closure) {
    if (!val) return -1;
    Py_ssize_t p = PyLong_AsSsize_t(val);
    if (p == -1 && PyErr_Occurred()) return -1;
    self->pos = p;
    return 0;
}

static PyObject* FastReader_get_length(FastReaderObject *self, void *closure) {
    return PyLong_FromSsize_t(self->length);
}

static int FastReader_set_length(FastReaderObject *self, PyObject *val, void *closure) {
    if (!val) return -1;
    Py_ssize_t l = PyLong_AsSsize_t(val);
    if (l == -1 && PyErr_Occurred()) return -1;
    self->length = l;
    return 0;
}

static PyObject* FastReader_get_padded_length(FastReaderObject *self, void *closure) {
    if (self->padded_length) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

static int FastReader_set_padded_length(FastReaderObject *self, PyObject *val, void *closure) {
    if (!val) return -1;
    int is_true = PyObject_IsTrue(val);
    if (is_true == -1) return -1;
    self->padded_length = is_true;
    return 0;
}

static PyGetSetDef FastReader_getset[] = {
    {"eof", (getter)FastReader_get_eof, NULL, "Whether cursor is at or past end", NULL},
    {"remaining", (getter)FastReader_get_remaining, NULL, "Unconsumed input slice", NULL},
    {"text", (getter)FastReader_get_text, (setter)FastReader_set_text, "Mangled string", NULL},
    {"pos", (getter)FastReader_get_pos, (setter)FastReader_set_pos, "Current cursor offset", NULL},
    {"length", (getter)FastReader_get_length, (setter)FastReader_set_length, "End of input offset", NULL},
    {"padded_length", (getter)FastReader_get_padded_length, (setter)FastReader_set_padded_length, "Leading zero flag", NULL},
    {NULL}
};

static PyMethodDef FastReader_methods[] = {
    {"peek", (PyCFunction)FastReader_peek, METH_NOARGS, "Peek next character"},
    {"ahead", (PyCFunction)FastReader_ahead, METH_VARARGS, "Peek character at offset"},
    {"peek2", (PyCFunction)FastReader_peek2, METH_NOARGS, "Peek next 2 characters"},
    {"ahead2", (PyCFunction)FastReader_ahead2, METH_VARARGS, "Peek 2 characters at offset"},
    {"startswith", (PyCFunction)FastReader_startswith, METH_VARARGS, "Prefix check"},
    {"take", (PyCFunction)FastReader_take, METH_NOARGS, "Consume next char"},
    {"take_exactly", (PyCFunction)FastReader_take_exactly, METH_VARARGS, "Consume count chars"},
    {"eat", (PyCFunction)FastReader_eat, METH_VARARGS, "Consume literal if present"},
    {"expect", (PyCFunction)FastReader_expect, METH_VARARGS, "Consume literal or raise"},
    {"digits", (PyCFunction)FastReader_digits, METH_NOARGS, "Consume decimal run"},
    {"length_prefixed", (PyCFunction)FastReader_length_prefixed, METH_NOARGS, "Consume length-prefixed chunk"},
    {"number", (PyCFunction)FastReader_number, METH_VARARGS | METH_KEYWORDS, "Consume number"},
    {"integer", (PyCFunction)FastReader_integer, METH_VARARGS | METH_KEYWORDS, "Consume integer"},
    {"seq_id", (PyCFunction)FastReader_seq_id, METH_NOARGS, "Consume seq-id"},
    {"fail", (PyCFunction)FastReader_fail, METH_VARARGS, "Raise ParseError"},
    {NULL}
};

static PyObject* FastReader_repr(FastReaderObject *self) {
    PyObject *rem = FastReader_get_remaining(self, NULL);
    PyObject *cur = (self->pos <= self->length && self->pos >= 0) ? PyUnicode_Substring(self->text, 0, self->pos) : PyUnicode_New(0,0);
    PyObject *repr = PyUnicode_FromFormat("Reader(%R | %R)", cur, rem);
    Py_XDECREF(rem);
    Py_XDECREF(cur);
    return repr;
}

static PyTypeObject FastReaderType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "demangle._speedups.FastReader",
    .tp_doc = "Accelerated cursor over mangled name",
    .tp_basicsize = sizeof(FastReaderObject),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)FastReader_init,
    .tp_dealloc = (destructor)FastReader_dealloc,
    .tp_repr = (reprfunc)FastReader_repr,
    .tp_methods = FastReader_methods,
    .tp_getset = FastReader_getset,
};

static PyObject* fast_msvc_identifier(PyObject *self, PyObject *args) {
    PyObject *text;
    Py_ssize_t pos;
    Py_ssize_t length;
    if (!PyArg_ParseTuple(args, "Unn", &text, &pos, &length)) return NULL;
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    Py_ssize_t at = pos;
    while (at < length) {
        if (PyUnicode_READ(kind, data, at) == '@') break;
        at++;
    }
    if (at >= length || at == pos) {
        Py_RETURN_NONE;
    }
    PyObject *sub = PyUnicode_Substring(text, pos, at);
    if (!sub) return NULL;
    PyObject *new_pos = PyLong_FromSsize_t(at + 1);
    PyObject *res = PyTuple_Pack(2, new_pos, sub);
    Py_DECREF(new_pos);
    Py_DECREF(sub);
    return res;
}

static PyObject* fast_itanium_source_name(PyObject *self, PyObject *args) {
    PyObject *text;
    Py_ssize_t pos;
    Py_ssize_t length;
    int is_ascii;
    if (!PyArg_ParseTuple(args, "Unnp", &text, &pos, &length, &is_ascii)) return NULL;
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    if (pos >= length) Py_RETURN_NONE;
    Py_UCS4 first = PyUnicode_READ(kind, data, pos);
    if (first < '0' || first > '9') Py_RETURN_NONE;
    Py_ssize_t start = pos;
    pos++;
    long long count = first - '0';
    while (pos < length) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, pos);
        if (ch >= '0' && ch <= '9') {
            count = count * 10 + (ch - '0');
            pos++;
            if (pos - start > 20) Py_RETURN_NONE;
        } else {
            break;
        }
    }
    if (count == 0) Py_RETURN_NONE;
    if (pos < length && PyUnicode_READ(kind, data, pos) == '_') Py_RETURN_NONE;
    if (!is_ascii) Py_RETURN_NONE;
    Py_ssize_t stop = pos + count;
    if (stop > length) Py_RETURN_NONE;
    PyObject *name = PyUnicode_Substring(text, pos, stop);
    if (!name) return NULL;
    PyObject *new_pos = PyLong_FromSsize_t(stop);
    PyObject *res = PyTuple_Pack(2, new_pos, name);
    Py_DECREF(new_pos);
    Py_DECREF(name);
    return res;
}

static PyMethodDef SpeedupMethods[] = {
    {"fast_msvc_identifier", fast_msvc_identifier, METH_VARARGS, "Fast MSVC @-delimited identifier scan"},
    {"fast_itanium_source_name", fast_itanium_source_name, METH_VARARGS, "Fast Itanium source name scan"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef speedupsmodule = {
    PyModuleDef_HEAD_INIT,
    "_speedups",
    "Compiled C accelerator for demangle hot paths",
    -1,
    SpeedupMethods
};

PyMODINIT_FUNC PyInit__speedups(void) {
    PyObject *m = PyModule_Create(&speedupsmodule);
    if (!m) return NULL;
    if (PyType_Ready(&FastReaderType) < 0) {
        Py_DECREF(m);
        return NULL;
    }
    Py_INCREF(&FastReaderType);
    if (PyModule_AddObject(m, "FastReader", (PyObject *)&FastReaderType) < 0) {
        Py_DECREF(&FastReaderType);
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
