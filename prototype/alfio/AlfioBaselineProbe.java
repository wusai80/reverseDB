package research.alfio;

import alfio.config.*;
import alfio.manager.*;
import alfio.manager.user.UserManager;
import alfio.model.*;
import alfio.model.metadata.AlfioMetadata;
import alfio.model.modification.*;
import alfio.model.modification.support.LocationDescriptor;
import alfio.model.system.ConfigurationKeys;
import alfio.model.transaction.PaymentProxy;
import alfio.model.user.*;
import alfio.repository.*;
import alfio.repository.system.ConfigurationRepository;
import alfio.repository.user.OrganizationRepository;
import alfio.util.ClockProvider;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.aop.support.AopUtils;
import org.springframework.beans.factory.support.AbstractBeanDefinition;
import org.springframework.beans.factory.support.BeanDefinitionRegistry;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;

import java.math.BigDecimal;
import java.net.URI;
import java.net.http.*;
import java.nio.file.*;
import java.sql.*;
import java.time.*;
import java.util.*;

/** Original release classes only. Fixture construction follows upstream IntegrationTestUtil.
 * No business implementation or repository is replaced. Not an HTTP authorization test.
 */
public final class AlfioBaselineProbe {
  static final Instant NOW=Instant.parse("2026-11-06T10:00:00Z");
  static final ObjectMapper JSON=new ObjectMapper();
  static Path output;
  static final List<Map<String,Object>> checks=new ArrayList<>();
  static void require(boolean ok,String label) { if(!ok) throw new AssertionError(label); checks.add(Map.of("check",label,"passed",true)); }
  static void save(String file,Object value) throws Exception { JSON.writerWithDefaultPrettyPrinter().writeValue(output.resolve(file).toFile(),value); }
  static List<Map<String,Object>> rows(Connection c,String sql) throws Exception {
    try(Statement s=c.createStatement(); ResultSet r=s.executeQuery(sql)) {
      List<Map<String,Object>> rows=new ArrayList<>();
      while(r.next()) {
        Map<String,Object> row=new LinkedHashMap<>();
        for(int i=1;i<=r.getMetaData().getColumnCount();i++) row.put(r.getMetaData().getColumnLabel(i),r.getObject(i));
        rows.add(row);
      }
      return rows;
    }
  }
  static Map<String,Object> state(Connection c,String label) throws Exception {
    Map<String,Object> result=new LinkedHashMap<>();
    for(var row:rows(c,"SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")) {
      String table=(String)row.get("tablename");
      String quoted="\""+table.replace("\"","\"\"")+"\"";
      result.put(table,rows(c,"SELECT count(*) AS rows,encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h COLLATE \"C\"),''),'UTF8')),'hex') AS sha256 FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM public."+quoted+" t) x").get(0));
    }
    save(label+"-state.json",result); return result;
  }
  static void inventory(Connection c,int free,int pending,String label) throws Exception {
    inventory(c,free,pending,0,label);
  }
  static void inventory(Connection c,int free,int pending,int released,String label) throws Exception {
    var actual=rows(c,"SELECT count(*) total,count(*) FILTER(WHERE status='FREE') free,count(*) FILTER(WHERE status='PENDING') pending,count(*) FILTER(WHERE status='RELEASED') released,count(*) FILTER(WHERE tickets_reservation_id IS NOT NULL) linked FROM ticket").get(0);
    save(label+"-inventory.json",actual); state(c,label);
    require(((Number)actual.get("total")).intValue()==20,label+": all twenty tickets retained");
    require(((Number)actual.get("free")).intValue()==free && ((Number)actual.get("pending")).intValue()==pending && ((Number)actual.get("released")).intValue()==released && ((Number)actual.get("linked")).intValue()==pending,label+": inventory and reservation links");
  }
  static String reserve(TicketReservationManager manager,Event event,int category,int quantity) throws Exception {
    var request=new TicketReservationModification(); request.setAmount(quantity); request.setTicketCategoryId(category);
    return manager.createTicketReservation(event,List.of(new TicketReservationWithOptionalCodeModification(request,Optional.empty())),List.of(),java.util.Date.from(NOW.plus(Duration.ofDays(1))),Optional.empty(),Locale.ENGLISH,false,null);
  }
  public static void main(String[] args) throws Exception {
    output=Path.of(args[1]); Files.createDirectories(output);
    var application=new SpringApplication(SpringBootInitializer.class,BaseConfiguration.class,DataSourceConfiguration.class,WebSecurityConfig.class,MvcConfiguration.class);
    application.setAdditionalProfiles("spring-boot","dev","disable-jobs");
    application.setDefaultProperties(Map.of("server.address","127.0.0.1","server.port","0","datasource.url",args[0],"datasource.username","alfio_user","datasource.password",System.getenv("ALFIO_DB_PASSWORD"),"alfio.version","2.0-M5-2609"));
    // Replace only the upstream injectable application clock before singleton construction.
    // Legacy new Date()/UUID calls remain unmodified and their actual values are retained.
    application.addInitializers(context -> context.addBeanFactoryPostProcessor(factory -> {
      var definition=(AbstractBeanDefinition)((BeanDefinitionRegistry)factory).getBeanDefinition("clockProvider");
      definition.setInstanceSupplier(() -> ClockProvider.init(Clock.fixed(NOW,ZoneOffset.UTC)));
    }));
    try(var context=application.run(); Connection observer=DriverManager.getConnection(args[0],"alfio_user",System.getenv("ALFIO_DB_PASSWORD"))) {
      require(ClockProvider.clock().instant().equals(NOW),"fixed injected application clock");
      var role=rows(observer,"SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").get(0);
      require(Boolean.FALSE.equals(role.get("rolsuper")) && Boolean.FALSE.equals(role.get("rolbypassrls")),"application role neither superuser nor BYPASSRLS"); save("role.json",role);
      var web=(ServletWebServerApplicationContext)context;
      var client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
      var response=client.send(HttpRequest.newBuilder(URI.create("http://127.0.0.1:"+web.getWebServer().getPort()+"/api/v2/info")).timeout(Duration.ofSeconds(15)).GET().build(),HttpResponse.BodyHandlers.ofString());
      save("http-info.json",Map.of("status",response.statusCode(),"body",response.body())); require(response.statusCode()==200,"original HTTP info endpoint");
      var config=context.getBean(ConfigurationRepository.class);
      for(var e:Map.of(ConfigurationKeys.BASE_URL,"http://localhost:8080",ConfigurationKeys.SUPPORTED_LANGUAGES,"7",ConfigurationKeys.INVOICE_ADDRESS,"INVOICE_ADDRESS",ConfigurationKeys.VAT_NR,"42").entrySet()) {
        config.deleteByKey(e.getKey().getValue()); config.insert(e.getKey().getValue(),e.getValue(),"");
      }
      var users=context.getBean(UserManager.class); var organizations=context.getBean(OrganizationRepository.class);
      users.createOrganization(new OrganizationModification(null,"reversedb-baseline","org@example.invalid","org",null,null),null);
      int org=organizations.findByName("reversedb-baseline").orElseThrow().getId();
      String username="reversedb-operator";
      users.insertUser(org,username,"Test","Operator","operator@example.invalid",Role.OPERATOR,User.Type.INTERNAL,null);
      users.insertUser(org,username+"_owner","Test","Owner","owner@example.invalid",Role.OWNER,User.Type.INTERNAL,null);
      var start=LocalDateTime.ofInstant(NOW,ZoneOffset.UTC);
      var categories=List.of(new TicketCategoryModification(null,"baseline-category",TicketCategory.TicketAccessType.INHERIT,20,
          new DateTimeModification(start.minusDays(1).toLocalDate(),start.toLocalTime()),
          new DateTimeModification(start.plusDays(4).toLocalDate(),start.toLocalTime()),Map.of("en","baseline"),BigDecimal.TEN,false,"",true,null,null,null,null,null,0,null,null,AlfioMetadata.empty()));
      var modification=new EventModification(null,Event.EventFormat.IN_PERSON,"url","url","url","privacy","url",null,"reversedb-event","Baseline event",org,
          "location","0.0","0.0","UTC",Map.of("en","baseline","it","baseline","de","baseline"),
          new DateTimeModification(start.plusDays(5).toLocalDate(),start.toLocalTime()),new DateTimeModification(start.plusDays(5).toLocalDate(),start.plusHours(1).toLocalTime()),
          BigDecimal.TEN,"CHF",20,BigDecimal.ONE,true,List.of(PaymentProxy.OFFLINE),categories,false,new LocationDescriptor("","","",""),7,null,null,AlfioMetadata.empty(),List.of());
      var events=context.getBean(EventManager.class); events.createEvent(modification,username);
      var event=events.getSingleEvent("reversedb-event",username);
      int category=context.getBean(TicketCategoryRepository.class).findAllTicketCategories(event.getId()).get(0).getId();
      var manager=context.getBean(TicketReservationManager.class); var repository=context.getBean(TicketRepository.class);
      require(AopUtils.isAopProxy(manager),"actual Spring transaction proxy retained");
      save("loaded-beans.json",Map.of("manager",manager.getClass().getName(),"repository",repository.getClass().getName(),"originalManagerLocation",TicketReservationManager.class.getProtectionDomain().getCodeSource().getLocation().toString()));
      inventory(observer,20,0,"00-initial");
      // Explicit RLS policy check, separate from manager calls without an HTTP user context.
      observer.setAutoCommit(false);
      try(Statement s=observer.createStatement()) {
        s.execute("SET LOCAL alfio.checkRowAccess = true"); s.execute("SET LOCAL alfio.currentUserOrgs = '-1'");
        require(((Number)rows(observer,"SELECT count(*) n FROM event").get(0).get("n")).intValue()==0,"RLS hides other organization event");
        s.execute("SET LOCAL alfio.currentUserOrgs = '"+org+"'");
        require(((Number)rows(observer,"SELECT count(*) n FROM event").get(0).get("n")).intValue()==1,"RLS permits matching organization event");
      } finally { observer.rollback(); observer.setAutoCommit(true); }
      String a=reserve(manager,event,category,3); inventory(observer,17,3,"01-reserve-three");
      var summary=manager.orderSummaryForReservationId(a,event);
      require(summary.getTicketAmount()==3 && "30.00".equals(summary.getTotalPrice()),"three-ticket order summary");
      save("02-query.json",Map.of("reservation",a,"tickets",summary.getTicketAmount(),"total",summary.getTotalPrice()));
      var before=state(observer,"03-before-rejected-reservation");
      try { reserve(manager,event,category,21); throw new AssertionError("Oversell accepted"); }
      catch(alfio.manager.support.reservation.NotEnoughTicketsException expected) { save("03-rejection.json",Map.of("exception",expected.getClass().getName())); }
      require(before.equals(state(observer,"03-after-rejected-reservation")),"insufficient stock rolls back all public table rows");
      String b=reserve(manager,event,category,2); inventory(observer,15,5,"04-reserve-two");
      var processor=context.getBean(WaitingQueueSubscriptionProcessor.class);
      manager.cancelPendingReservation(a,false,username); inventory(observer,15,2,3,"05-cancel-first-released");
      processor.handleWaitingTickets(); inventory(observer,18,2,"05-process-released-first");
      require(manager.orderSummaryForReservationId(b,event).getTicketAmount()==2,"second reservation survives first cancellation");
      save("06-query.json",Map.of("reservation",b,"tickets",2));
      manager.cancelPendingReservation(b,false,username); inventory(observer,18,0,2,"07-cancel-second-released");
      processor.handleWaitingTickets(); inventory(observer,20,0,"07-process-released-second");
      String c=reserve(manager,event,category,1); inventory(observer,19,1,"08-reserve-released-ticket");
      manager.cancelPendingReservation(c,false,username); inventory(observer,19,0,1,"09-cancel-third-released");
      processor.handleWaitingTickets(); inventory(observer,20,0,"09-final");
      require(((Number)rows(observer,"SELECT count(*) n FROM tickets_reservation").get(0).get("n")).intValue()==0,"no pending reservations remain");
      save("summary.json",Map.of("status","passed","checks",checks,"operations",12,"fixtureTickets",20,"reservationIds",List.of(a,b,c),"scope","original Spring service entry with real transactions and three explicit upstream waiting-queue processing steps; HTTP info only; not full business HTTP authorization or optimized differential"));
    } finally { save("checks.json",checks); }
  }
}
