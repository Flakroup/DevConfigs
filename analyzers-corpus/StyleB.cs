using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using JetBrains.Annotations;

namespace Corpus;

//# _header

//# ArrayWithDefaultValuesInitialization #1
public class B_Awd1 { public int[] A = new int[] { 0, 0, 0 }; }
//# ArrayWithDefaultValuesInitialization #2
public class B_Awd2 { public bool[] A = new bool[] { false, false }; }
//# ArrayWithDefaultValuesInitialization #3
public class B_Awd3 { public string?[] A = { null, null }; }

//# CatchClauseWithoutVariable #1
public class B_Ccv1 { public void M() { try { Console.Write(1); } catch (Exception) { Console.Write(2); } } }
//# CatchClauseWithoutVariable #2
public class B_Ccv2 { public void M() { try { Console.Write(1); } catch (Exception) when (DateTime.Now.Year > 2000) { Console.Write(2); } } }
//# CatchClauseWithoutVariable #3
public class B_Ccv3 { public void M() { try { Console.Write(1); } catch (Exception ex) { Console.Write(ex.Message); } } }

//# ComplexObjectDestructuringProblem #1
public class B_Cod1 { public void M(Cod_Obj o, Serilog.ILogger log) { log.Information("Obj {@Obj}", o); } }
//# ComplexObjectDestructuringProblem #2
public class B_Cod2 { public void M(string s, Serilog.ILogger log) { log.Information("Obj {@Obj}", s); } }
//# ComplexObjectDestructuringProblem #3
public class B_Cod3 { public void M(Cod_Obj o, Serilog.ILogger log) { log.Information("Obj {$Obj}", o); } }
//# ComplexObjectDestructuringProblem #4
public class B_Cod4 { public void M(Cod_Obj o, Serilog.ILogger log) { log.Information("Obj {Obj}", o); } }
//# ComplexObjectDestructuringProblem #5
public class B_Cod5 { public void M(List<Cod_Obj> o, Serilog.ILogger log) { log.Information("Obj {Obj}", o); } }
//# ComplexObjectDestructuringProblem #6
public class B_Cod6 { public void M(Cod_Obj o, Serilog.ILogger log) { log.Information("Obj {@Obj} {@Obj}", o); } }

//# ComplexObjectInContextDestructuringProblem #1
public class B_Codc1 { public void M(Cod_Obj o) { Serilog.Log.ForContext("Ctx", o, true).Information("x"); } }
//# ComplexObjectInContextDestructuringProblem #2
public class B_Codc2 { public void M(Cod_Obj o) { Serilog.Log.ForContext("Ctx", o, false).Information("x"); } }
//# ComplexObjectInContextDestructuringProblem #3
public class B_Codc3 { public void M(string o) { Serilog.Log.ForContext("Ctx", o, true).Information("x"); } }

//# ConvertToConstant.Global #1
public class B_Ctg1 { public int G = 5; }
//# ConvertToConstant.Global #2
public class B_Ctg2 { public static readonly int G = 5; }
//# ConvertToConstant.Global #3
public class B_Ctg3 { internal readonly string G = "a"; }
//# ConvertToConstant.Global #4
public class B_Ctg4 { protected static int G = 5; }

//# EmptyEmbeddedStatement #1
public class B_Ees1 { public void M(bool b) { while (b) ; } }
//# EmptyEmbeddedStatement #2
public class B_Ees2 { public void M(bool b, int[] a) { foreach (int x in a) ; } }
//# EmptyEmbeddedStatement #3
public class B_Ees3 { public void M(bool b) { if (b) Console.Write(1); else ; } }
//# EmptyEmbeddedStatement #4
public class B_Ees4 { public void M(object o) { lock (o) ; } }

//# EnforceDoWhileStatementBraces
public class B_Edw { public void M(bool b) { do
    Console.Write(
        1);
while (b); } }

//# EnforceFixedStatementBraces
public unsafe class B_Efx { public void M(int[] a) { fixed (int* p = a)
    Console.Write(
        *p); } }

//# EnforceForeachStatementBraces
public class B_Efe { public void M(int[] a) { foreach (int x in a)
    Console.Write(
        x); } }

//# EnforceForStatementBraces
public class B_Efr { public void M() { for (int i = 0; i < 3; i++)
    Console.Write(
        i); } }

//# EnforceIfStatementBraces
public class B_Eif { public void M(bool b) { if (b)
    Console.Write(
        1); } }

//# EnforceLockStatementBraces
public class B_Elk { private readonly object _o = new(); public void M() { lock (_o)
    Console.Write(
        1); } }

//# EnforceUsingStatementBraces
public class B_Eus { public void M(IDisposable d) { using (d)
    Console.Write(
        1); } }

//# EnforceWhileStatementBraces
public class B_Ewh { public void M(bool b) { while (b)
    Console.Write(
        1); } }

//# InconsistentNaming #1
public class B_Icn1 { public void badName() { } }
//# InconsistentNaming #2
public class B_Icn2 { private int Field; public int G() => Field; }

//# InvokeAsExtensionMember #1
public static class B_IaeExt { public static int Twice(this int x) => x * 2; }
public class B_Iae1 { public int M(int i) => B_IaeExt.Twice(i); }
//# InvokeAsExtensionMember #2
public class B_Iae2 { public int M(List<int> l) => Enumerable.Count(l); }

//# NotifyPropertyChangedInvocatorFromConstructor #1
public class B_Npc1 : System.ComponentModel.INotifyPropertyChanged
{
    public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;
    [NotifyPropertyChangedInvocator]
    protected void OnPropertyChanged([System.Runtime.CompilerServices.CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(name));
    private int _p;
    public int P { get => _p; set { _p = value; OnPropertyChanged(); } }
    public B_Npc1() { OnPropertyChanged(); P = 1; }
}

//# ParameterOnlyUsedForPreconditionCheck.Global #1
public class B_Pog1 { public void M(int x) { if (x < 0) throw new ArgumentOutOfRangeException(nameof(x)); } }
//# ParameterOnlyUsedForPreconditionCheck.Global #2
public class B_Pog2 { public void M(string? s) { ArgumentNullException.ThrowIfNull(s); } }
//# ParameterOnlyUsedForPreconditionCheck.Local #1
public class B_Pol1 { private void M(int x) { if (x < 0) throw new ArgumentOutOfRangeException(nameof(x)); } public void G() => M(1); }
//# ParameterOnlyUsedForPreconditionCheck.Local #2
public class B_Pol2 { private void M(string? s) { ArgumentNullException.ThrowIfNull(s); } public void G() => M(""); }

//# AnnotationRedundancyAtValueType
public class B_Ran1 { [NotNull] public int Ra = 0; }
//# RedundantNullnessAttributeWithNullableReferenceTypes #1
public class B_Ran2 { [CanBeNull] public string? Ra = ""; }
//# RedundantNullnessAttributeWithNullableReferenceTypes #2
public class B_Ran3 { [NotNull] public string Ra() => ""; }
//# RedundantNullnessAttributeWithNullableReferenceTypes #3
public class B_Ran4 { public void M([NotNull] string s) { Console.Write(s); } }
//# RedundantNullnessAttributeWithNullableReferenceTypes #4
public class B_Ran5 { [CanBeNull] public int? Ra() => 1; }

//# RedundantAssertionStatement #1
public class B_Ras1 { public void M(string? s) { if (s != null) { Debug.Assert(s != null); } } }
//# RedundantAssertionStatement #2
public class B_Ras2 { public void M() { Debug.Assert(true); } }
//# RedundantAssertionStatement #3
public class B_Ras3 { public void M(string s) { Debug.Assert(s is not null); Console.Write(s); } }
//# RedundantAssertionStatement #4
public class B_Ras4 { public void M(string s) { if (s == null) return; ArgumentNullException.ThrowIfNull(s); } }

//# RedundantAwait #1
public class B_Raw1 { private Task F() => Task.CompletedTask; public async Task M() { await F(); } }
//# RedundantAwait #2
public class B_Raw2 { private Task<int> F() => Task.FromResult(1); public async Task<int> M() => await F(); }
//# RedundantAwait #3
public class B_Raw3 { private Task<int> F() => Task.FromResult(1); public async Task<int> M() { return await F().ConfigureAwait(false); } }

//# RedundantBaseQualifier
public class B_RbqBase { public void X() { } }
public class B_Rbq : B_RbqBase { public void M() { base.X(); } }

//# RedundantCast.0 #1
public class B_Rca1 { public object M() => (object)"a"; }
//# RedundantCast.0 #2
public class B_Rca2 { public IEnumerable<int> M(List<int> l) => (IEnumerable<int>)l; }
//# RedundantCast.0 #3
public class B_Rca3 { public long M(int i) => (long)i + (long)2; }
//# RedundantCast.0 #4
public class B_Rca4 { public int M(object o) => (int)(int)o; }
//# RedundantCast.0 #5
public class B_Rca5 { public long M(int i) { long x = (long)i; return x; } }

//# RedundantDeclarationSemicolon #2
public enum B_Rds2 { A };
//# RedundantDeclarationSemicolon #3
public struct B_Rds3 { };

//# RedundantDefaultMemberInitializer #1
public class B_Rdm1 { private int _f = 0; public int G() => _f; }
//# RedundantDefaultMemberInitializer #2
public class B_Rdm2 { public int P { get; set; } = 0; }
//# RedundantDefaultMemberInitializer #3
public class B_Rdm3 { public string? S = null; public bool B = false; }

//# RedundantDelegateInvoke #1
public class B_Rdi1 { public int M(Func<int> f) { return f.Invoke(); } }
//# RedundantDelegateInvoke #2
public class B_Rdi2 { public event Action? E; public void M() { E.Invoke(); } }

//# RedundantDiscardedPattern #1
public class B_Rdp1 { public bool M((int, int) t) => t is (_, _); }
//# RedundantDiscardedPattern #2
public class B_Rdp2 { public int M(object o) => o switch { string => 1, _ => 2 }; }
//# RedundantDiscardedPattern #3
public class B_Rdp3 { public bool M(object o) => o is string and not null or _; }
//# RedundantDiscardedPattern #4
public class B_Rdp4 { public bool M(string o) => o is { Length: _ }; }
//# RedundantDiscardedPattern #5
public class B_Rdp5 { public bool M(object o) => o is var _; }

//# RedundantFixedPointerDeclaration #2
public unsafe class B_Rfp2 { public void M(string s) { fixed (char* p = &s.GetPinnableReference()) { Console.Write(*p); } } }
//# RedundantFixedPointerDeclaration #3
public unsafe class B_Rfp3 { public void M(int[] a) { fixed (int* p = &a[0]) Console.Write(*p); } }

//# RedundantInlineAssertion #1
public class B_Ria1 { public int M(string s) { return s!.Length; } }
//# RedundantInlineAssertion #2
public class B_Ria2 { public int M(string s) { return Contract_.Assert(s).Length; } }
public static class Contract_ { public static T Assert<T>(T t) => t; }

//# RedundantOverflowCheckingContext #1
public class B_Roo1 { public int M() { const int c = unchecked(1 + 2); return c; } }
//# RedundantOverflowCheckingContext #2
public class B_Roo2 { public int M(int i) { unchecked { unchecked { return i + 1; } } } }
//# RedundantOverflowCheckingContext #3
public class B_Roo3 { public int M(int i) { return checked(i); } }
//# RedundantOverflowCheckingContext #4
public class B_Roo4 { public int M(int i) { unchecked { return i + 1; } } }

//# RedundantOverriddenMember
public class B_Rom { public override int GetHashCode() => base.GetHashCode(); }

//# ArrangeDefaultValueWhenTypeNotEvident #1
public class B_Rtd1 { public int M() { return default(int); } }
//# ArrangeDefaultValueWhenTypeNotEvident #2
public class B_Rtd2 { public void P(int i) { Console.Write(i); } public void M() { P(default(int)); } }
//# ArrangeDefaultValueWhenTypeNotEvident #3
// Compliant on purpose: the shared layer pins the default literal, so neither successor may report this one.
public class B_Rtd4 { public int M() { return default; } public int F = default; }
//# ArrangeDefaultValueWhenTypeEvident
public class B_Rtd3 { public int F = default(int); }

//# RemoveRedundantOrStatement.False #1
public class B_Rof1 { public bool M(bool a) { a |= false; return a; } }
//# RemoveRedundantOrStatement.False #2
public class B_Rof2 { public bool M(bool a) { a = a || false; return a; } }
//# RemoveRedundantOrStatement.False #3
public class B_Rof3 { public bool M(bool a, bool b) { a = a | false; return a == b; } }

//# RemoveRedundantOrStatement.True #1
public class B_Rot1 { public bool M(bool a) { a |= true; return a; } }
//# RemoveRedundantOrStatement.True #2
public class B_Rot2 { public bool M(bool a) { a = a || true; return a; } }

//# UnusedLocalFunctionParameter #1
public class B_Ulf1 { public int M() { return L(1, 2); static int L(int p, int q) => p; } }
//# UnusedLocalFunctionParameter #2
public class B_Ulf2 { public int M() { int L(int p, int q) => p; return L(1, 2); } }
//# UnusedLocalFunctionParameter #3
public class B_Ulf3 { public Action M() { void L(int p) { } return () => L(1); } }

//# UnusedMemberHierarchy.Local #1
public class B_Umh1 { private abstract class B { public abstract void V(); } private class D : B { public override void V() { } } }
//# UnusedMemberHierarchy.Local #2
public class B_Umh2 { private class B { public virtual void V() { } } private class D : B { public override void V() { } } public int G() => new D().GetHashCode(); }
//# UnusedMemberHierarchy.Local #3
public class B_Umh3 { private interface I { void V(); } private class D : I { public void V() { } } public int G() => new D().GetHashCode(); }

//# UseNameofExpressionForPartOfTheString #1
public class B_Unp1 { public void M(int count) { if (count < 0) throw new ArgumentException("count must not be negative"); } }
//# UseNameofExpressionForPartOfTheString #2
public class B_Unp2 { public int Count; public string M() => "Count: " + Count; }
//# UseNameofExpressionForPartOfTheString #3
public class B_Unp3 { public int Count; public void M() { Console.Write("The property Count is wrong"); } }
//# UseNameofExpressionForPartOfTheString #4
public class B_Unp4 { public void M(string name) { throw new ArgumentException("Invalid value of name"); } }
