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

//# AccessToStaticMemberViaDerivedType
public class Ast_Base { public static int Value; }
public class Ast_Derived : Ast_Base { }
public class Ast_Use { public int M() => Ast_Derived.Value; }

//# ArrangeModifiersOrder
public class Amo { static public int F() => 1; }

//# ArrangeRedundantParentheses
public class Arp { public int M(int a) { int r = (a + 1); return (r); } }

//# ArrangeStaticMemberQualifier
public class Asq { public static int S() => 1; public int M() => Asq.S(); }

//# ArrangeThisQualifier
public class Atq { private int _f; public void M() { this._f = 1; } public int G() => _f; }

//# ArrangeTypeMemberModifiers
public class Atm { int _f; public int G() => _f; }

//# ArrangeTypeModifiers
class Atmod { }

//# ArrayWithDefaultValuesInitialization
public class Awd { public int[] A = new int[3] { 0, 0, 0 }; }

//# BaseMemberHasParams
public class Bmp_Base { public virtual void M(params int[] a) { } }
public class Bmp_Der : Bmp_Base { public override void M(int[] a) { } }

//# BuiltInTypeReferenceStyle
public class Bts { public Int32 F; }

//# BuiltInTypeReferenceStyleForMemberAccess
public class Btm { public int M() => Int32.MaxValue; }

//# ByRefArgumentIsVolatileField
public class Bva { private volatile int _v; private static void R(ref int x) { x++; } public void M() => R(ref _v); }

//# CatchClauseWithoutVariable #1
public class Ccv1 { public void M() { try { Console.Write(1); } catch (InvalidOperationException) { Console.Write(2); } } }
//# CatchClauseWithoutVariable #2
public class Ccv2 { public void M() { try { Console.Write(1); } catch { Console.Write(2); } } }

//# ComplexObjectDestructuringProblem
public class Cod_Obj { public int A; public Cod_Obj? Child; }
public class Cod { public void M(Cod_Obj o) { Serilog.Log.Information("Obj {Obj}", o); } }

//# ComplexObjectInContextDestructuringProblem
public class Codc { public void M(Cod_Obj o) { Serilog.Log.ForContext("Ctx", o).Information("x"); } }

//# ConvertClosureToMethodGroup
public class Cc { private static int F(int x) => x; public IEnumerable<int> M(IEnumerable<int> e) => e.Select(x => F(x)); }

//# ConvertToConstant.Global
public class Ctg { public readonly int Cg = 5; }

//# ConvertToConstant.Local #1
public class Ctl1 { private readonly int _cl = 5; public int G() => _cl; }
//# ConvertToConstant.Local #2
public class Ctl2 { public int M() { int x = 5; return x + 1; } }

//# CSharpWarnings::CS0108,CS0114 #1
public class W108_Base { public void M() { } }
public class W108_Der : W108_Base { public void M() { } }
//# CSharpWarnings::CS0108,CS0114 #2
public class W114_Base { public virtual void V() { } }
public class W114_Der : W114_Base { public void V() { } }

//# CSharpWarnings::CS0109
public class W109 { public new int Nothing; }

//# CSharpWarnings::CS0184
public class W184 { public bool M(int i) => i is string; }

//# CSharpWarnings::CS0420
public class W420 { private volatile int _v; private static void R(ref int x) { x++; } public void M() => R(ref _v); }

//# CSharpWarnings::CS0628
public sealed class W628 { protected int F; }

//# CSharpWarnings::CS0659
public class W659 { public override bool Equals(object? obj) => true; }

//# CSharpWarnings::CS0672
public class W672_Base { [Obsolete] public virtual void M() { } }
public class W672_Der : W672_Base { public override void M() { } }

//# CSharpWarnings::CS4014
public class W4014 { public async Task F() { await Task.Yield(); } public void M() { F(); } }

//# EmptyEmbeddedStatement
public class Ees { public void M(bool b) { if (b) ; } }

//# EmptyForStatement
public class Efs { public void M() { for (int i = 0; i < 3; i++) ; } }

//# EmptyStatement
public class Es { public void M() { Console.Write(1);; } }

//# EnforceDoWhileStatementBraces
public class Edw { public void M(bool b) { do
    Console.Write(1);
while (b); } }

//# EnforceFixedStatementBraces
public unsafe class Efx { public void M(int[] a) { fixed (int* p = a)
    Console.Write(*p); } }

//# EnforceForeachStatementBraces
public class Efe { public void M(int[] a) { foreach (int x in a)
    Console.Write(x); } }

//# EnforceForStatementBraces
public class Efr { public void M() { for (int i = 0; i < 3; i++)
    Console.Write(i); } }

//# EnforceIfStatementBraces
public class Eif { public void M(bool b) { if (b)
    Console.Write(1); } }

//# EnforceLockStatementBraces
public class Elk { private readonly object _o = new(); public void M() { lock (_o)
    Console.Write(1); } }

//# EnforceUsingStatementBraces
public class Eus { public void M(IDisposable d) { using (d)
    Console.Write(1); } }

//# EnforceWhileStatementBraces
public class Ewh { public void M(bool b) { while (b)
    Console.Write(1); } }

//# InconsistentNaming
public class Icn { public void bad_name() { } }

//# InvokeAsExtensionMethod
public class Iae { public int M(List<int> l) => Enumerable.First(l); }

//# LocalVariableHidesMember
public class Lvh { private int _count; public int M() { int _count = 1; return _count; } public int G() => _count; }

//# LoopVariableIsNeverChangedInsideLoop #1
public class Lv1 { public void M() { bool c = true; while (c) { Console.Write(1); } } }
//# LoopVariableIsNeverChangedInsideLoop #2
public class Lv2 { public void M() { for (int i = 0; i < 3;) { Console.Write(i); } } }

//# MemberHidesStaticFromOuterClass
public class Mhs { public static int Value; public class Inner { public int Value; } }

//# MethodHasAsyncOverload
public class Mha { public void Do() { } public Task DoAsync() => Task.CompletedTask; public async Task M() { Do(); await Task.Yield(); } }

//# MethodHasAsyncOverloadWithCancellation
public class Mhc { public void Do2() { } public Task Do2Async(CancellationToken ct) => Task.CompletedTask; public async Task M(CancellationToken ct) { Do2(); await Task.Yield(); } }

//# MethodOverloadWithOptionalParameter
public class Mow { public void Mo(int a = 0) { Console.Write(a); } public void Mo() { } }

//# MethodSupportsCancellation
public class Msc { public void Run(int a) { Console.Write(a); } public void Run(int a, CancellationToken ct) { Console.Write(ct); } public void M(CancellationToken ct) { Run(1); Console.Write(ct); } }

//# NonAtomicCompoundOperator
public class Nac { private volatile int _v; public void M() { _v++; } }

//# NotifyPropertyChangedInvocatorFromConstructor
public class Npc : System.ComponentModel.INotifyPropertyChanged
{
    public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;
    [NotifyPropertyChangedInvocator] protected void OnPropertyChanged(string? name = null) => PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(name));
    public Npc() { OnPropertyChanged(nameof(P)); }
    public int P { get; set; }
}

//# ObjectCreationAsStatement
public class Oca { public void M() { new Oca(); } }

//# OptionalParameterHierarchyMismatch
public class Oph_Base { public virtual void M(int a = 1) { Console.Write(a); } }
public class Oph_Der : Oph_Base { public override void M(int a = 2) { Console.Write(a); } }

//# OptionalParameterRefOut
public class Opr { public void M([Optional] ref int a) { a++; } }

//# ParameterHidesMember
public class Phm { private int value; public int G() => value; public void M(int value) { Console.Write(value); } }

//# ParameterOnlyUsedForPreconditionCheck.Global
public class Pog { public void M(string s) { if (string.IsNullOrEmpty(s)) throw new ArgumentException("empty", nameof(s)); Console.Write(1); } }

//# ParameterOnlyUsedForPreconditionCheck.Local
public class Pol { private void M(string s) { Debug.Assert(s.Length > 0); Console.Write(1); } public void G() => M("a"); }

//# PartialMethodParameterNameMismatch
public partial class Pmm { partial void M(int a); }
public partial class Pmm { partial void M(int b) { Console.Write(b); } public void G() => M(1); }

//# PartialMethodWithSinglePart
public partial class Pms { partial void Only(); public void G() => Only(); }

//# PartialTypeWithSinglePart
public partial class Pts { }

//# RedundantAnnotation
public class Ran { [NotNull] public string Ra = ""; }

//# RedundantAnonymousTypePropertyName
public class Rap { public object M(int a) => new { a = a }; }

//# RedundantArgumentDefaultValue
public class Rad { public void M(int a = 5) { Console.Write(a); } public void G() => M(5); }

//# RedundantAssertionStatement
public class Ras { public void M(string s) { Debug.Assert(s != null); Console.Write(s); } }

//# RedundantAssignment
public class Rasn { public int M() { int x = 1; x = 2; return x; } }

//# RedundantAttributeParentheses
[Obsolete()]
public class Rap2 { }

//# RedundantAwait
public class Raw { public async Task<int> M() { return await Task.FromResult(1); } }

//# RedundantBaseConstructorCall
public class Rbc_Base { public Rbc_Base() { } }
public class Rbc : Rbc_Base { public Rbc() : base() { } }

//# RedundantBaseQualifier
public class Rbq { public string M() => base.ToString() ?? ""; }

//# RedundantBoolCompare
public class Rbo { public bool M(bool b) => b == true; }

//# RedundantCaseLabel
public class Rcl { public void M(int i) { switch (i) { case 1: Console.Write(1); break; case 2: default: Console.Write(2); break; } } }

//# RedundantCast
public class Rca { public string M(string s) => (string)s; }

//# RedundantCast.0
public class Rca0 { public int M(int i) => (int)(i + 1); }

//# RedundantCatchClause
public class Rcc { public void M() { try { Console.Write(1); } catch (Exception) { throw; } } }

//# RedundantCheckBeforeAssignment
public class Rcb { private int _f; public void M() { if (_f != 1) _f = 1; } }

//# RedundantCollectionInitializerElementBraces
public class Rce { public List<int> L = new() { { 1 }, { 2 } }; }

//# RedundantDeclarationSemicolon
public class Rds { };

//# RedundantDefaultMemberInitializer
public class Rdm { public int F = 0; }

//# RedundantDelegateCreation
public class Rdc { public event EventHandler? E; private void H(object? s, EventArgs a) { } public void M() { E += new EventHandler(H); } }

//# RedundantDelegateInvoke
public class Rdi { public void M(Action a) { a.Invoke(); } }

//# RedundantDiscardDesignation
public class Rdd { public bool M(object o) => o is int _; }

//# RedundantDiscardedPattern
public class Rdp { public int M(object o) => o switch { string => 1, _ and not null => 2, _ => 3 }; }

//# RedundantEmptyFinallyBlock
public class Rfb { public void M() { try { Console.Write(1); } catch (IOException_) { Console.Write(2); } finally { } } }
public class IOException_ : Exception { }

//# RedundantEmptyObjectCreationArgumentList
public class Roc_T { public int P { get; set; } }
public class Roc { public Roc_T M() => new Roc_T() { P = 1 }; }

//# RedundantEmptyObjectOrCollectionInitializer
public class Rei { public Roc_T M() => new Roc_T { }; }

//# RedundantEmptySwitchSection
public class Res { public void M(int i) { switch (i) { case 1: Console.Write(1); break; default: break; } } }

//# RedundantEnumerableCastCall
public class Rec { public IEnumerable<int> M(IEnumerable<int> e) => e.Cast<int>(); }

//# RedundantExplicitArrayCreation
public class Rea { public int[] A = new int[] { 1, 2 }; }

//# RedundantExplicitArraySize
public class Reas { public int[] A = new int[2] { 1, 2 }; }

//# RedundantExplicitNullableCreation
public class Ren { public int? N = new int?(1); }

//# RedundantExplicitParamsArrayCreation
public class Rep { public void P(params int[] a) { Console.Write(a); } public void G() => P(new int[] { 1, 2 }); }

//# RedundantExplicitTupleComponentName
public class Rtn { public (int a, int b) M(int a, int b) => (a: a, b: b); }

//# RedundantFixedPointerDeclaration
public unsafe class Rfp { public void M(int[] arr) { fixed (int* p = &arr[0]) { Console.Write(*p); } } }

//# RedundantIfElseBlock
public class Rie { public int M(bool c) { if (c) { return 1; } else { return 2; } } }

//# RedundantInlineAssertion
public class Ria { public void M(string s) { ArgumentNullException.ThrowIfNull(s); Console.Write(s); } }

//# RedundantJumpStatement
public class Rjs { public void M() { Console.Write(1); return; } }

//# RedundantLambdaParameterType
public class Rlp { public Func<int, int> F = (int x) => x; }

//# RedundantLambdaSignatureParentheses
public class Rls { public Func<int, int> F = (x) => x; }

//# RedundantLogicalConditionalExpressionOperand
public class Rlc { public bool M(bool a) => a && true; }

//# RedundantNameQualifier
public class Rnq { public void M() { System.Console.Write(1); } }

//# RedundantOverflowCheckingContext
public class Roo { public int M(int i) => unchecked(i + 1); }

//# RedundantOverload.Global
public class Rog { public void M(int a, int b = 0) { Console.Write(a + b); } public void M(int a) { M(a, 0); } }

//# RedundantOverload.Local
public class Rol { private void M(int a, int b = 0) { Console.Write(a + b); } private void M(int a) { M(a, 0); } public void G() { M(1); M(1, 2); } }

//# RedundantOverriddenMember
public class Rom { public override string ToString() => base.ToString() ?? ""; }

//# RedundantPropertyPatternClause
public class Rpp { public bool M(object o) => o is string { }; }

//# RedundantQueryOrderByAscendingKeyword
public class Rqo { public IEnumerable<int> M(IEnumerable<int> e) => from x in e orderby x ascending select x; }

//# RedundantRangeBound
public class Rrb { public string M(string s) => s[1..^0]; }

//# RedundantStringFormatCall
public class Rsf { public string M() => string.Format("abc"); }

//# RedundantStringInterpolation
public class Rsi { public string M() => $"abc"; }

//# RedundantStringToCharArrayCall
public class Rsc { public void M(string s) { foreach (char c in s.ToCharArray()) Console.Write(c); } }

//# RedundantTernaryExpression
public class Rte { public bool M(bool b) => b ? true : false; }

//# RedundantToStringCall
public class Rts { public string M(string s) => "a" + s.ToString(); }

//# RedundantToStringCallForValueType
public class Rtv { public string M(int i) => "a" + i.ToString(); }

//# RedundantTypeArgumentsOfMethod
public class Rta { public T Id<T>(T t) => t; public int M() => Id<int>(1); }

//# RedundantTypeSpecificationInDefaultExpression
public class Rtd { public int M() { int d = default(int); return d; } }

//# RedundantUnsafeContext
public unsafe class Ruc { public unsafe void M() { } }

//# RedundantVerbatimPrefix
public class Rvp { public int M() { int @value = 1; return @value; } }

//# RedundantVerbatimStringPrefix
public class Rvs { public string M() => @"abc"; }

//# RemoveRedundantBraces #1
public class Rrbr1 { public void M(int i) { switch (i) { case 1: { Console.Write(1); break; } } } }
//# RemoveRedundantBraces #2
public class Rrbr2 { public void M(bool b) { if (b) { Console.Write(1); } } }

//# RemoveRedundantOrStatement.False
public class Rof { public bool M(bool a) => a || false; }

//# RemoveRedundantOrStatement.True
public class Rot { public bool M(bool a) => a || true; }

//# SuggestVarOrType_BuiltInTypes #1
public class Svb1 { public int M() { int x = 1; return x; } }
//# SuggestVarOrType_BuiltInTypes #2
public class Svb2 { public int M() { var x = 1; return x; } }

//# SuggestVarOrType_SimpleTypes #1
public class Svs1 { public object M() { Rts t = new Rts(); return t; } }
//# SuggestVarOrType_SimpleTypes #2
public class Svs2 { public object M() { var t = new Rts(); return t; } }

//# SuggestVarOrType_Elsewhere #1
public class Sve1 { private List<int> G() => new(); public object M() { List<int> t = G(); return t; } }
//# SuggestVarOrType_Elsewhere #2
public class Sve2 { private List<int> G() => new(); public object M() { var t = G(); return t; } }

//# ThreadStaticAtInstanceField
public class Tsi { [ThreadStatic] public int T; }

//# ThreadStaticFieldHasInitializer
public class Tsf { [ThreadStatic] public static int T = 1; }

//# UnassignedField.Compiler
public class Uf { private int _uf; public int M() => _uf; }

//# UnassignedReadonlyField
public class Ur { private readonly int _ur; public int M() => _ur; }

//# UnassignedReadonlyField.Compiler
public class Urc { internal readonly int Ur; public int M() => Ur; }

//# UnusedField.Compiler
public class Ufc { private int _unused; }

//# UnusedLocalFunctionParameter
public class Ulf { public int M() { int L(int p) => 1; return L(1); } }

//# UnusedMemberHierarchy.Local
public class Umh_Base { protected virtual void V() { } }
public class Umh_Der : Umh_Base { protected override void V() { } }

//# UnusedMember.Local
public class Uml { private void Unused() { } }

//# UnusedMethodReturnValue.Local
public class Umr { private int R() => 1; public void M() { R(); } }

//# UnusedParameterInPartialMethod
public partial class Upp { partial void P(int a); }
public partial class Upp { partial void P(int a) { } public void G() => P(1); }

//# UnusedParameter.Local
public class Upl { public void M() { Up(1); } private void Up(int a) { } }

//# UnusedTypeParameter
public class Utp { public void M<T>() { } }

//# UseCancellationTokenForIAsyncEnumerable
public class Uct { public async Task M(IAsyncEnumerable<int> e, CancellationToken ct) { await foreach (int x in e) { Console.Write(x); } Console.Write(ct); } }

//# UseCollectionCountProperty
public class Ucc { public int M(List<int> l) => l.Count(); }

//# UseNameofExpression
public class Une { public void M(object p) { if (p == null) throw new ArgumentNullException("p"); } }

//# UseNameofExpressionForPartOfTheString
public class Unp { public void M(int count) { if (count < 0) throw new ArgumentOutOfRangeException(nameof(count), "count must not be negative"); } }

//# UseNameOfInsteadOfTypeOf
public class Unt { public string M() => typeof(Unt).Name; }

//# ValueParameterNotUsed
public class Vpn { private int _p; public int P { get => _p; set { } } }

//# VariableHidesOuterVariable
public class Vho { public Func<int, int> M() { int x = 1; Console.Write(x); return x => x; } }

//# UseNameofForDependencyProperty
public class Und { public static readonly System.Windows.DependencyProperty P = System.Windows.DependencyProperty.Register("P", typeof(int), typeof(Und)); }
