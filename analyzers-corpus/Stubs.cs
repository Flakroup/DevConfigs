namespace System.Windows
{
    public class DependencyProperty
    {
        public static DependencyProperty Register(string name, System.Type propertyType, System.Type ownerType) => new();
    }
}
